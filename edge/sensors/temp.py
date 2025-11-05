# edge/sensors/temp.py
# Python 3.10+
# 목적: 라즈베리 파이의 온도 센서를 1Hz로 읽어 절대온도(℃) 샘플을 생성한다.
# - 기본 우선순위: 1‑Wire(DS18B20) → sysfs(thermal/hwmon) → mock
# - 재현성: 초당 1회 일정 간격(모노토닉 스케줄) + epoch ns 타임스탬프 + per-device seq
# - 안전성: 읽기 실패 시 valid=False로 플래그하고 스케줄은 유지(값은 전 샘플 유지 또는 NaN)
# - 이 모듈은 "센서 스트림"만 책임진다. MQTT Event 발행은 상위 계층에서 수행.

from __future__ import annotations

import glob
import math
import os
import signal
import sys
import time
from dataclasses import dataclass
from typing import Iterator, Optional, Tuple

__all__ = ["TempSensor", "Sample"]


@dataclass
class Sample:
    ts_ns: int            # 측정 시각(ns, epoch; tick 직후)
    seq: int              # per-device 단조 증가 시퀀스
    celsius: float        # 보정/스케일 적용 후 최종 온도(℃)
    source: str           # "w1" | "sysfs" | "mock"
    valid: bool           # 해당 측정의 유효성(읽기/CRC/범위)
    raw_path: Optional[str] = None  # 읽기 경로(디버그)


class _BaseSource:
    source_name = "base"
    raw_path: Optional[str] = None
    def read_celsius(self) -> Tuple[Optional[float], bool]:
        """온도(℃), valid를 반환. 실패 시 (None, False)."""
        raise NotImplementedError
    def close(self) -> None:
        pass


class _W1Source(_BaseSource):
    """1-Wire DS18B20: /sys/bus/w1/devices/28-*/w1_slave"""
    source_name = "w1"

    def __init__(self, w1_path: Optional[str] = None):
        if w1_path:
            self.raw_path = w1_path
        else:
            # 가장 먼저 찾은 28-* 디바이스 사용
            candidates = sorted(glob.glob("/sys/bus/w1/devices/28-*/w1_slave"))
            if not candidates:
                raise FileNotFoundError("No DS18B20 (w1) device found under /sys/bus/w1/devices")
            self.raw_path = candidates[0]

    def read_celsius(self) -> Tuple[Optional[float], bool]:
        try:
            with open(self.raw_path, "r", encoding="ascii", errors="strict") as f:
                lines = f.read().strip().splitlines()
            if len(lines) < 2:
                return (None, False)
            crc_ok = lines[0].endswith("YES")
            # 두 번째 줄의 't=xxxxx' (milli‑C)
            pos = lines[1].find("t=")
            if pos == -1:
                return (None, False)
            milli = int(lines[1][pos + 2 :])
            c = milli / 1000.0
            return (c, crc_ok)
        except Exception:
            return (None, False)


class _SysfsSource(_BaseSource):
    """
    generic sysfs: /sys/class/thermal/thermal_zone*/temp 또는
                   /sys/class/hwmon/hwmon*/temp*_input
    """
    source_name = "sysfs"

    def __init__(self, sysfs_path: Optional[str] = None):
        if sysfs_path:
            self.raw_path = sysfs_path
        else:
            # 우선 thermal_zone0 → hwmon 순
            tz = sorted(glob.glob("/sys/class/thermal/thermal_zone*/temp"))
            if tz:
                self.raw_path = tz[0]
            else:
                hw = sorted(glob.glob("/sys/class/hwmon/hwmon*/temp*_input"))
                if not hw:
                    raise FileNotFoundError("No sysfs thermal/hwmon temperature input found")
                self.raw_path = hw[0]

    def read_celsius(self) -> Tuple[Optional[float], bool]:
        try:
            with open(self.raw_path, "r", encoding="ascii", errors="strict") as f:
                s = f.read().strip()
            # 값 스케일: milli‑C 또는 ℃
            v = float(s)
            if v > 200.0:              # 대부분 milli‑C (예: 43750)
                return (v / 1000.0, True)
            return (v, True)
        except Exception:
            return (None, False)


class _MockSource(_BaseSource):
    """센서가 없을 때 개발/CI용. 완만한 사인파 + 잡음."""
    source_name = "mock"

    def __init__(self, base_c: float = 24.0, amp_c: float = 1.5, period_s: float = 300.0):
        self._t0 = time.monotonic()
        self._base = base_c
        self._amp = amp_c
        self._period = max(1.0, period_s)

    def read_celsius(self) -> Tuple[Optional[float], bool]:
        t = time.monotonic() - self._t0
        val = self._base + self._amp * math.sin(2 * math.pi * t / self._period)
        # ±0.05°C 정도의 미세 노이즈
        val += (time.time_ns() % 1000) / 1000.0 * 0.1 - 0.05
        return (val, True)


class TempSensor:
    """
    1Hz 온도 센서 스트림.
    - 출력: Sample(ts_ns, seq, celsius, source, valid)
    - 보정: celsius' = c_scale * celsius + c_offset (기본 1, 0)
    - 범위 체크: min_c/max_c 지정 시, 범위를 벗어나면 valid=False 처리(값은 유지하거나 NaN)
    """
    def __init__(
        self,
        device_id: str,
        backend: str = "auto",             # "auto" | "w1" | "sysfs" | "mock"
        sample_hz: float = 1.0,
        seq_start: int = 0,
        c_offset: float = 0.0,
        c_scale: float = 1.0,
        min_c: float | None = None,
        max_c: float | None = None,
        w1_path: str | None = None,
        sysfs_path: str | None = None,
    ):
        if sample_hz <= 0:
            raise ValueError("sample_hz must be positive")
        self.device_id = device_id
        self.sample_hz = float(sample_hz)
        self.period_ns = int(1e9 / self.sample_hz)
        self.seq = int(seq_start)
        self.c_offset = float(c_offset)
        self.c_scale = float(c_scale)
        self.min_c = min_c
        self.max_c = max_c
        self._last_value: float | None = None  # 읽기 실패 시 유지용
        # 백엔드 선택
        self._src: _BaseSource
        self._backend_name = None
        self._src = self._select_backend(backend, w1_path=w1_path, sysfs_path=sysfs_path)
        self._backend_name = self._src.source_name
        # 시그널 안전 종료
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    # ---------------- 내부 유틸 ----------------

    def _select_backend(self, backend: str, w1_path: str | None, sysfs_path: str | None) -> _BaseSource:
        be = backend.lower()
        if be == "w1":
            return _W1Source(w1_path=w1_path)
        if be == "sysfs":
            return _SysfsSource(sysfs_path=sysfs_path)
        if be == "mock":
            return _MockSource()
        # auto: w1 → sysfs → mock
        try:
            return _W1Source(w1_path=w1_path)
        except Exception:
            pass
        try:
            return _SysfsSource(sysfs_path=sysfs_path)
        except Exception:
            pass
        sys.stderr.write("[temp] auto: falling back to mock source (no hardware)\n")
        return _MockSource()

    # ---------------- 공개 API ----------------

    def stream(self, duration_s: float | None = None) -> Iterator[Sample]:
        """
        지정 시간 동안(또는 무한) 1Hz 간격으로 샘플을 생성한다.
        타임스탬프는 읽기 직후의 epoch ns. 스케줄링은 monotonic 기반으로 틱 지연을 최소화.
        """
        start_mono = time.monotonic_ns()
        deadline = None if duration_s is None else time.time_ns() + int(duration_s * 1e9)
        n = 0
        while True:
            # 종료 체크(벽시계 기준; 일정 시간 후 종료)
            if deadline is not None and time.time_ns() >= deadline:
                return
            # 다음 틱까지 슬립
            target_ns = start_mono + n * self.period_ns
            now_ns = time.monotonic_ns()
            if target_ns > now_ns:
                time.sleep((target_ns - now_ns) / 1e9)
            n += 1
            # 읽기
            raw_c, ok = self._src.read_celsius()
            ts_ns = time.time_ns()
            valid = bool(ok)
            if raw_c is None:
                # 읽기 실패: 마지막 값 또는 NaN 유지
                val_c = float("nan") if self._last_value is None else self._last_value
                valid = False
            else:
                val_c = raw_c
            # 보정/스케일
            val_c = self.c_scale * val_c + self.c_offset
            # 범위 체크(옵션)
            if valid and (self.min_c is not None and val_c < self.min_c or
                          self.max_c is not None and val_c > self.max_c):
                valid = False  # 비정상 값
            # 전 샘플 유지 정책: invalid면 last_value 유지(값 자체는 유지하되 플래그로 식별)
            if not valid and self._last_value is not None:
                val_c = self._last_value
            else:
                self._last_value = val_c
            s = Sample(
                ts_ns=ts_ns,
                seq=self.seq,
                celsius=float(val_c),
                source=self._backend_name,
                valid=valid,
                raw_path=getattr(self._src, "raw_path", None),
            )
            self.seq += 1
            yield s

    def close(self) -> None:
        try:
            self._src.close()
        except Exception:
            pass

    # ---------------- 기타 ----------------

    def _handle_signal(self, signum, frame) -> None:
        try:
            self.close()
        finally:
            raise SystemExit(0)

    def __repr__(self) -> str:
        return (f"TempSensor(device_id={self.device_id!r}, hz={self.sample_hz}, "
                f"backend={self._backend_name}, path={getattr(self._src, 'raw_path', None)!r})")


# ---------------- CLI(현장 점검용) ----------------
def main():
    import argparse
    p = argparse.ArgumentParser(description="1Hz Temperature sensor stream (DS18B20/sysfs/mock)")
    p.add_argument("--device-id", required=True)
    p.add_argument("--backend", choices=["auto", "w1", "sysfs", "mock"], default="auto")
    p.add_argument("--sample-hz", type=float, default=1.0)
    p.add_argument("--duration-s", type=float, default=30.0)
    p.add_argument("--c-offset", type=float, default=0.0, help="온도 보정 오프셋(℃)")
    p.add_argument("--c-scale", type=float, default=1.0, help="온도 스케일 팩터")
    p.add_argument("--min-c", type=float, default=None)
    p.add_argument("--max-c", type=float, default=None)
    p.add_argument("--w1-path", default=None, help="예: /sys/bus/w1/devices/28-xxxx/w1_slave")
    p.add_argument("--sysfs-path", default=None, help="예: /sys/class/thermal/thermal_zone0/temp")
    args = p.parse_args()

    sensor = TempSensor(
        device_id=args.device_id,
        backend=args.backend,
        sample_hz=args.sample_hz,
        c_offset=args.c_offset,
        c_scale=args.c_scale,
        min_c=args.min_c,
        max_c=args.max_c,
        w1_path=args.w1_path,
        sysfs_path=args.sysfs_path,
    )
    print(f"[temp] start: {sensor!r}")
    count = 0
    try:
        for s in sensor.stream(duration_s=args.duration_s):
            v = f"{s.celsius:6.3f}" if s.celsius == s.celsius else "  NaN "  # NaN 출력 처리
            print(f"[temp] ts={s.ts_ns} seq={s.seq:6d} T={v} °C valid={s.valid} src={s.source} path={s.raw_path}")
            count += 1
    finally:
        sensor.close()
        print(f"[temp] done, samples={count}")


if __name__ == "__main__":
    main()
