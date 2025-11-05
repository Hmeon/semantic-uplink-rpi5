# edge/sensors/mic_rms.py
# Python 3.10+
# 목적: USB 마이크를 100ms 프레임으로 샘플링하여 RMS dBFS 값을 생성한다.
# - 프라이버시 준수: 원음(PCM) 저장/전송 금지, 통계량(dBFS)만 산출.  [과제 제안서/동결안]  # noqa
# - 재현성: 프레임 경계마다 ns 타임스탬프(time.time_ns), per-device 시퀀스(seq) 보장.
# - 안정성: sounddevice(옵션) → arecord(기본) 순으로 백엔드 선택.
# - 무음/클리핑 대비: clip_ratio(절대치가 임계치 이상인 샘플 비율) 산출.
# - 이 모듈은 "센서 스트림"만 책임진다. Event MQTT 발행은 상위(예: edge_daemon/predict/uploader)에서 수행.

from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Iterator, Optional

import numpy as np

__all__ = ["MicRMS", "Sample"]


@dataclass
class Sample:
    """100ms 프레임 하나에 대한 센서 샘플."""
    ts_ns: int         # 프레임 종료 시각(ns, epoch)
    seq: int           # per-device 단조 증가 시퀀스(프레임마다 +1)
    dbfs: float        # RMS dBFS (0 dBFS <= peak; RMS는 통상 -3 dBFS 근처)
    clip_ratio: float  # |x| >= clip_threshold*FS 비율(0.0~1.0)


class _AudioSource:
    """오디오 프레임(int16 mono) 공급자 인터페이스."""
    def read_frame(self) -> Optional[np.ndarray]:
        raise NotImplementedError
    def close(self) -> None:
        pass


class _SoundDeviceSource(_AudioSource):
    """sounddevice가 설치된 경우 활용(선호). 없으면 arecord로 대체."""
    def __init__(self, samplerate: int, frame_samples: int, device: Optional[str] = None):
        try:
            import sounddevice as sd  # type: ignore
        except Exception as e:
            raise RuntimeError("sounddevice backend unavailable") from e
        self._sd = sd
        self._samplerate = samplerate
        self._frame = frame_samples
        self._stream = sd.InputStream(
            channels=1,
            dtype="int16",
            samplerate=samplerate,
            blocksize=frame_samples,
            device=device
        )
        self._stream.start()

    def read_frame(self) -> Optional[np.ndarray]:
        data, overflowed = self._stream.read(self._frame)
        # data: shape (frame, 1), dtype int16
        if overflowed:
            # 오버런 발생 시에도 데이터는 유효. 경고만 출력.
            sys.stderr.write("[mic_rms] sounddevice overflow\n")
        return np.asarray(data[:, 0], dtype=np.int16)

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass


class _ARecordSource(_AudioSource):
    """arecord(alsa-utils)에 의존하는 백엔드. RPi에서 기본 사용 가능성이 높다."""
    def __init__(self, samplerate: int, frame_samples: int, device: Optional[str] = None):
        self._samplerate = samplerate
        self._frame_bytes = frame_samples * 2  # int16 mono
        dev = device or "default"
        # -t raw 로 stdout에 PCM을 기록 → 파이썬에서 프레임 단위로 읽음
        cmd = [
            "arecord",
            "-q",
            "-D", f"{dev}",
            "-c", "1",
            "-f", "S16_LE",
            "-r", str(samplerate),
            "-t", "raw",
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=self._frame_bytes,
            )
            if self._proc.stdout is None:
                raise RuntimeError("arecord stdout not available")
        except FileNotFoundError as e:
            raise RuntimeError("arecord not found. Install alsa-utils or use sounddevice backend.") from e
        self._stdout = self._proc.stdout
        self._buf = bytearray()

    def read_frame(self) -> Optional[np.ndarray]:
        # 정확히 frame_bytes만큼 누적해 프레임 생성
        while len(self._buf) < self._frame_bytes:
            chunk = self._stdout.read(self._frame_bytes - len(self._buf))
            if chunk is None:
                # EOF
                return None
            if not chunk:
                time.sleep(0.001)
                continue
            self._buf.extend(chunk)
        raw = self._buf[:self._frame_bytes]
        del self._buf[:self._frame_bytes]
        arr = np.frombuffer(raw, dtype="<i2")  # little-endian int16
        return arr

    def close(self) -> None:
        try:
            if getattr(self, "_proc", None):
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        except Exception:
            pass


class MicRMS:
    """
    USB 마이크에서 100ms 간격으로 RMS dBFS를 산출하는 센서.
    - 출력: Sample(ts_ns, seq, dbfs, clip_ratio)
    - seq는 인스턴스 내에서 단조 증가(초기값 seq_start).
    - db_offset으로 마이크/경로 이득 보정(단위: dB).
    """
    def __init__(
        self,
        device_id: str,
        sample_rate: int = 16_000,
        frame_ms: int = 100,
        seq_start: int = 0,
        db_offset: float = 0.0,
        clip_threshold: float = 0.999,   # 0.0~1.0, 1.0에 가까울수록 엄격
        backend: str = "auto",           # "auto" | "sounddevice" | "arecord"
        arecord_device: str | None = None,
        sounddevice_device: str | None = None,
    ):
        if frame_ms <= 0 or sample_rate <= 0:
            raise ValueError("sample_rate and frame_ms must be positive")
        self.device_id = device_id
        self.sample_rate = int(sample_rate)
        self.frame_ms = int(frame_ms)
        self.frame_samples = int(round(self.sample_rate * self.frame_ms / 1000.0))
        self.seq = int(seq_start)
        self.db_offset = float(db_offset)
        self.clip_threshold = float(clip_threshold)
        if not (0.0 < self.clip_threshold <= 1.0):
            raise ValueError("clip_threshold must be in (0, 1]")
        # 백엔드 결정
        self._src: _AudioSource
        self._backend_name = None
        if backend == "sounddevice" or (backend == "auto" and _has_sounddevice()):
            self._src = _SoundDeviceSource(self.sample_rate, self.frame_samples, device=sounddevice_device)
            self._backend_name = "sounddevice"
        else:
            self._src = _ARecordSource(self.sample_rate, self.frame_samples, device=arecord_device)
            self._backend_name = "arecord"
        # 시그널 시 안전 종료
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

    # ---------- 공개 API ----------

    def stream(self, duration_s: float | None = None) -> Iterator[Sample]:
        """
        100ms마다 Sample을 생성한다. duration_s가 None이면 무한 스트림.
        타임스탬프(ts_ns)는 프레임이 수집 완료된 시점.
        """
        start_ns = time.time_ns()
        deadline_ns = None if duration_s is None else start_ns + int(duration_s * 1e9)
        full_scale = 32768.0  # int16 full-scale
        thr_val = int(self.clip_threshold * (full_scale - 1))
        eps = 1e-12

        while True:
            if deadline_ns is not None and time.time_ns() >= deadline_ns:
                return
            frame = self._src.read_frame()
            if frame is None:
                return
            ts_ns = time.time_ns()
            # int16 -> float in [-1, 1)
            x = frame.astype(np.float32) / full_scale
            # RMS -> dBFS
            rms = float(np.sqrt(np.mean(x * x)))
            dbfs = 20.0 * math.log10(max(rms, eps)) + self.db_offset
            # clipping 비율
            clip_ratio = float(np.mean(np.abs(frame) >= thr_val))
            s = Sample(ts_ns=ts_ns, seq=self.seq, dbfs=dbfs, clip_ratio=clip_ratio)
            self.seq += 1
            yield s

    def close(self) -> None:
        """오디오 소스/프로세스를 정리한다."""
        try:
            self._src.close()
        except Exception:
            pass

    # ---------- 내부 ----------

    def _handle_signal(self, signum, frame) -> None:
        try:
            self.close()
        finally:
            # 상위에서 더 처리할 수 있도록 기본 동작 유지
            raise SystemExit(0)

    def __repr__(self) -> str:
        return (f"MicRMS(device_id={self.device_id!r}, sr={self.sample_rate}, "
                f"frame_ms={self.frame_ms}, backend={self._backend_name})")


def _has_sounddevice() -> bool:
    try:
        import sounddevice  # type: ignore
        _ = sounddevice
        return True
    except Exception:
        return False


# ---------- CLI(로컬 검증용) ----------
# 주: 별도 모듈 추가 없이 현 파일만으로 현장 검증 가능하도록 최소 CLI 제공.
# 출력은 JSON Lines가 아니라 사람이 읽기 쉬운 텍스트로 제한(로그는 상위에서 관리).

def main():
    import argparse
    parser = argparse.ArgumentParser(description="MicRMS 100ms RMS(dBFS) streamer (no raw audio)")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--backend", choices=["auto", "sounddevice", "arecord"], default="auto")
    parser.add_argument("--arecord-device", default=None, help="arecord -D 인자 (예: plughw:1,0)")
    parser.add_argument("--sounddevice-device", default=None, help="sounddevice device name/id")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--frame-ms", type=int, default=100)
    parser.add_argument("--duration-s", type=float, default=5.0)
    parser.add_argument("--db-offset", type=float, default=0.0, help="마이크 경로 보정(dB)")
    parser.add_argument("--clip-threshold", type=float, default=0.999)
    args = parser.parse_args()

    mic = MicRMS(
        device_id=args.device_id,
        sample_rate=args.sample_rate,
        frame_ms=args.frame_ms,
        db_offset=args.db_offset,
        clip_threshold=args.clip_threshold,
        backend=args.backend,
        arecord_device=args.arecord_device,
        sounddevice_device=args.sounddevice_device,
    )
    print(f"[mic_rms] start: {mic!r}")
    count = 0
    try:
        for s in mic.stream(duration_s=args.duration_s):
            print(f"[mic_rms] ts={s.ts_ns} seq={s.seq} dbfs={s.dbfs:6.2f} dBFS clip={s.clip_ratio*100:5.2f}%")
            count += 1
    finally:
        mic.close()
        print(f"[mic_rms] done, frames={count}")


if __name__ == "__main__":
    # 환경에서 모듈 실행 시(테스트/현장 캘리브레이션 목적)
    main()
