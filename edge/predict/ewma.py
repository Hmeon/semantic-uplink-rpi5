# edge/predict/ewma.py
# Python 3.10+
# 목적: EWMA 기반 고정 τ SoD(trigger) + 하트비트를 수행하여 EventMsg를 생성한다.
# - 입력: mic_rms.Sample(dbfs) 또는 temp.Sample(celsius) Iterator
# - 출력: common.schema.EventMsg (퍼블리시는 상위 업로더가 담당)
# - 전송값은 kbits 균일 양자화(대표값, 실수). 잔차는 "원시값-직전예측"의 절대값.
# - 첫 샘플 부트스트랩/하트비트/최소 재전송 간격 등 실전 운용 세부 포함.
# - 스키마/토픽/프로파일/지표 정의는 동결안·과제 제안서와 일치.  # noqa

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Iterator, Optional, Tuple

from common.schema import (
    EventMsg,
    SensorType,
    LinkProfile,
    PolicyMode,
)
from common.quantize import quantizer_for_sensor

__all__ = ["EWMAConfig", "EWMAPredictor"]


@dataclass(slots=True, frozen=True)
class EWMAConfig:
    """
    EWMA 고정 τ 구성.
    - alpha: 0<α≤1  (예: mic 0.2, temp 0.5 권장)
    - tau: 전송 임계값 (단위: 센서 단위. mic=dB, temp=°C)
    - kbits: 1..16  (전송 양자화 레벨)
    - heartbeat_s: 하트비트 최소 주기(초). None 또는 0이면 비활성.
    - min_emit_interval_ms: 이벤트 최소 간격(ms) 가드(폭주 방지).
    - vmin/vmax: 센서 범위 재정의(기본은 quantize 모듈의 권고값).
    - bootstrap_emit: 첫 샘플 시 1회 전송(재현성/초기화).
    """
    device_id: str
    sensor: SensorType
    alpha: float
    tau: float
    kbits: int
    profile: LinkProfile
    heartbeat_s: float | None = 10.0
    min_emit_interval_ms: int = 0
    vmin: float | None = None
    vmax: float | None = None
    bootstrap_emit: bool = True


class EWMAPredictor:
    """
    EWMA(one-step ahead) 예측 및 임계값 트리거/하트비트 관리.
    - 내부 상태: last_pred, last_emit_ns
    - 트리거: |x_raw - last_pred| > τ  (업데이트는 전송 여부와 무관하게 매 샘플 수행)
    """
    def __init__(self, cfg: EWMAConfig):
        if not (0.0 < cfg.alpha <= 1.0):
            raise ValueError("alpha must be in (0, 1]")
        if cfg.tau < 0:
            raise ValueError("tau must be >= 0")
        if cfg.min_emit_interval_ms < 0:
            raise ValueError("min_emit_interval_ms must be >= 0")

        self.cfg = cfg
        self._q = quantizer_for_sensor(cfg.sensor, cfg.kbits, vmin=cfg.vmin, vmax=cfg.vmax)
        self._last_pred: float | None = None
        self._last_emit_ns: int | None = None
        self._boot_emitted: bool = False

    # ---------------- 공용 API ----------------

    def predict_and_maybe_emit(self, sample: Any) -> EventMsg | None:
        """
        센서 샘플 하나를 입력 받아, 조건을 만족하면 EventMsg를 생성.
        - mic_rms.Sample: (ts_ns, seq, dbfs, clip_ratio)
        - temp.Sample   : (ts_ns, seq, celsius, valid)
        """
        ts_ns, seq, x_raw, value_valid = self._extract_value(sample)
        if not value_valid:
            # 읽기 실패/비정상 값: 상태는 유지하고 전송하지 않음(하트비트는 별도 타이머로 처리).
            self._update_ewma(None)
            return None

        # 직전 예측(없으면 x_raw로 부트스트랩)
        last_pred = x_raw if self._last_pred is None else self._last_pred
        resid = abs(x_raw - last_pred)

        # 전송 여부 판단
        now_ns = ts_ns  # 센서 프레임 종결 시각을 전송 시각으로 사용
        emit_due_to_resid = resid > self.cfg.tau
        emit_due_to_boot = (self.cfg.bootstrap_emit and not self._boot_emitted and self._last_pred is None)
        emit_due_to_hb = False
        if not emit_due_to_resid and not emit_due_to_boot:
            if self.cfg.heartbeat_s and self.cfg.heartbeat_s > 0:
                if (self._last_emit_ns is None) or (now_ns - self._last_emit_ns >= int(self.cfg.heartbeat_s * 1e9)):
                    emit_due_to_hb = True

        should_emit = emit_due_to_resid or emit_due_to_boot or emit_due_to_hb

        # 최소 간격 가드
        if should_emit and self.cfg.min_emit_interval_ms > 0 and self._last_emit_ns is not None:
            if now_ns - self._last_emit_ns < int(self.cfg.min_emit_interval_ms * 1e6):
                should_emit = False  # rate-limit

        evt: EventMsg | None = None
        if should_emit:
            qv = self._q.quantize(x_raw).q  # 대표값(실수)
            evt = EventMsg(
                ts=int(ts_ns),
                seq=int(seq),                     # (device_id, sensor, seq)로 de-dup
                device_id=self.cfg.device_id,
                sensor=self.cfg.sensor,
                val=float(qv),
                pred=float(last_pred),
                res=float(resid),
                tau=float(self.cfg.tau),
                kbits=int(self.cfg.kbits),
                profile=self.cfg.profile,
                policy=PolicyMode.FIXED_TAU,
                aoi_ms=None,                      # 수집기 기준 계산. 엣지에서는 기록 생략.
            )
            self._last_emit_ns = now_ns
            if emit_due_to_boot:
                self._boot_emitted = True

        # 상태 업데이트(EWMA)
        self._update_ewma(x_raw)
        return evt

    def run(self, sample_iter: Iterator[Any], duration_s: float | None = None) -> Iterator[EventMsg]:
        """
        샘플 이터레이터를 소비하며 EventMsg를 yield.
        duration_s가 주어지면 그 시간 경과 후 종료(샘플 ts 기준이 아니라 벽시계 기준).
        """
        end_ns = None if duration_s is None else (time.time_ns() + int(duration_s * 1e9))
        for s in sample_iter:
            if end_ns is not None and time.time_ns() >= end_ns:
                return
            evt = self.predict_and_maybe_emit(s)
            if evt is not None:
                yield evt

    def close(self) -> None:
        """상태 정리(현재는 유지할 리소스 없음)."""
        return

    # ---------------- 내부 유틸 ----------------

    def _extract_value(self, sample: Any) -> Tuple[int, int, float, bool]:
        """
        지원 샘플:
          - mic_rms.Sample(ts_ns:int, seq:int, dbfs:float, clip_ratio:float)
          - temp.Sample   (ts_ns:int, seq:int, celsius:float, valid:bool)
        반환: (ts_ns, seq, x_raw, valid)
        """
        # duck-typing으로 의존 축소
        ts_ns = int(getattr(sample, "ts_ns"))
        seq = int(getattr(sample, "seq"))
        if self.cfg.sensor == SensorType.MIC_RMS:
            x = float(getattr(sample, "dbfs"))
            # mic_rms는 항상 valid로 간주(clip_ratio는 별도 메타)
            valid = True
        elif self.cfg.sensor == SensorType.TEMP:
            x = float(getattr(sample, "celsius"))
            valid = bool(getattr(sample, "valid"))
        else:
            raise ValueError(f"unsupported sensor: {self.cfg.sensor}")
        if not math.isfinite(x):
            # NaN/Inf 방지
            valid = False
        return ts_ns, seq, x, valid

    def _update_ewma(self, x_raw: float | None) -> None:
        """x_raw가 None이면 업데이트 없이 유지."""
        if x_raw is None:
            return
        if self._last_pred is None:
            self._last_pred = float(x_raw)
        else:
            a = float(self.cfg.alpha)
            self._last_pred = a * float(x_raw) + (1.0 - a) * float(self._last_pred)
