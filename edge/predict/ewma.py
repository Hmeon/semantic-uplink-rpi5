# edge/predict/ewma.py
# Python 3.10+
# 목적: EWMA 기반 고정 τ SoD(trigger) + 하트비트를 수행하여 EventMsg를 생성한다.
# - 입력: mic_rms.Sample(dbfs) 또는 temp.Sample(celsius) Iterator
# - 출력: common.schema.EventMsg (퍼블리시는 상위 업로더가 담당)
# - 전송값은 kbits 균일 양자화(대표값, 실수). 잔차는 "원시값-직전예측"의 절대값.
# - 첫 샘플 부트스트랩/하트비트/최소 재전송 간격 등 실전 운용 세부 포함.
# - 스키마/토픽/프로파일/지표 정의는 동결안·과제 제안서와 일치.  # noqa

"""EWMA predictor and event trigger for edge sampling.

Computes residuals against a one-step EWMA predictor and emits events when
threshold/heartbeat conditions are met. This module is performance-sensitive
and must keep per-sample work low to avoid sensor backpressure.
"""

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
    """Configuration for EWMA predictor and trigger thresholds.

    Args:
        device_id: Device identifier included in emitted events.
        sensor: Sensor type (mic_rms or temp).
        alpha: EWMA smoothing factor in (0, 1].
        tau: Residual threshold that triggers an event.
        kbits: Quantization bit width (1..16).
        profile: Link profile label for event metadata.
        heartbeat_s: Minimum emit interval for keep-alives; None disables.
        min_emit_interval_ms: Rate-limit for event emission (ms).
        vmin/vmax: Optional sensor range overrides for quantizer.
        bootstrap_emit: Emit once on first valid sample to establish baseline.
        diagnostics_enabled: Enable extra diagnostics fields in events.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Values are validated when EWMAPredictor is instantiated.

    Failure Modes:
        - Invalid values raise when building the predictor.
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
    diagnostics_enabled: bool = False


class EWMAPredictor:
    """EWMA predictor with threshold/heartbeat triggering.

    Args:
        cfg: EWMAConfig with thresholds, quantization, and metadata.

    Returns:
        None.

    Raises:
        ValueError: If configuration values are out of bounds.

    Contract:
        - Maintains last prediction and last emit time across samples.
        - Emits when residual exceeds tau, on heartbeat, or when forced.

    Side Effects:
        - Updates internal EWMA state and emission counters.

    Failure Modes:
        - Invalid samples are ignored for emission but still update EWMA state.
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
        self._q_cache: dict[int, Any] = {int(cfg.kbits): self._q}
        self._last_pred: float | None = None
        self._last_emit_ns: int | None = None
        self._boot_emitted: bool = False
        self._rate_limit_skips: int = 0

    # ---------------- 공용 API ----------------

    def preview(self, sample: Any) -> tuple[int, int, float, bool, float, float]:
        """Compute residuals without mutating predictor state.

        Args:
            sample: Sensor sample object.

        Returns:
            (ts_ns, seq, x_raw, valid, last_pred, resid) tuple.

        Raises:
            None.

        Side Effects:
            - None.

        Contract:
            - Does not update EWMA state; safe to call multiple times per sample.

        Failure Modes:
            - Non-finite inputs are surfaced as invalid via `valid` flag.
        """
        ts_ns, seq, x_raw, value_valid = self._extract_value(sample)
        last_pred = x_raw if self._last_pred is None else self._last_pred
        resid = abs(x_raw - last_pred)
        return ts_ns, seq, x_raw, value_valid, last_pred, resid

    def predict_and_maybe_emit(
        self,
        sample: Any,
        *,
        override_tau: float | None = None,
        override_kbits: int | None = None,
        policy_mode: PolicyMode | None = None,
        override_heartbeat_s: float | None = None,
        override_min_emit_ms: int | None = None,
        force_emit: bool = False,
        force_reason: str | None = None,
    ) -> EventMsg | None:
        """Update predictor state and emit EventMsg when thresholds fire.

        Args:
            sample: Sensor sample object.
            override_tau: Optional override for threshold tau.
            override_kbits: Optional override for quantization bits.
            policy_mode: Policy mode to record in event metadata.
            override_heartbeat_s: Optional heartbeat override.
            override_min_emit_ms: Optional rate-limit override.
            force_emit: Force an emission regardless of thresholds.
            force_reason: Optional reason string for diagnostics.

        Returns:
            EventMsg if emission occurs, otherwise None.

        Raises:
            ValueError: If configuration values are out of bounds.

        Side Effects:
            - Updates EWMA state and emission counters.

        Contract:
            - Emits only when threshold/heartbeat/force conditions are met.
            - Rate limiting can suppress threshold-driven emissions.

        Failure Modes:
            - Invalid samples short-circuit emission and update EWMA with None.
        """
        ts_ns, seq, x_raw, value_valid = self._extract_value(sample)
        if not value_valid:
            # 읽기 실패/비정상 값: 상태는 유지하고 전송하지 않음(하트비트는 별도 타이머로 처리).
            self._update_ewma(None)
            return None

        # 구성/오버라이드 적용
        tau = self.cfg.tau if override_tau is None else float(override_tau)
        kbits = self.cfg.kbits if override_kbits is None else int(override_kbits)
        hb_s = self.cfg.heartbeat_s if override_heartbeat_s is None else override_heartbeat_s
        min_emit_ms = (
            self.cfg.min_emit_interval_ms
            if override_min_emit_ms is None
            else int(override_min_emit_ms)
        )
        policy = PolicyMode.FIXED_TAU if policy_mode is None else policy_mode

        # 직전 예측(없으면 x_raw로 부트스트랩)
        last_pred = x_raw if self._last_pred is None else self._last_pred
        resid = abs(x_raw - last_pred)

        # 전송 여부 판단
        now_ns = ts_ns  # 센서 프레임 종결 시각을 전송 시각으로 사용
        emit_due_to_resid = resid > tau
        emit_due_to_boot = (
            self.cfg.bootstrap_emit
            and not self._boot_emitted
            and self._last_pred is None
        )
        emit_due_to_hb = False
        if not emit_due_to_resid and not emit_due_to_boot:
            if hb_s and hb_s > 0:
                if (self._last_emit_ns is None) or (now_ns - self._last_emit_ns >= int(hb_s * 1e9)):
                    emit_due_to_hb = True

        should_emit = emit_due_to_resid or emit_due_to_boot or emit_due_to_hb
        emit_due_to_force = bool(force_emit)
        if emit_due_to_force:
            should_emit = True

        # 최소 간격 가드
        if (
            should_emit
            and not emit_due_to_force
            and min_emit_ms > 0
            and self._last_emit_ns is not None
        ):
            if now_ns - self._last_emit_ns < int(min_emit_ms * 1e6):
                should_emit = False  # rate-limit
                if bool(self.cfg.diagnostics_enabled):
                    self._rate_limit_skips += 1

        evt: EventMsg | None = None
        if should_emit:
            event_reason: str | None = None
            if bool(self.cfg.diagnostics_enabled):
                if emit_due_to_force:
                    event_reason = str(force_reason or "FORCE")
                elif emit_due_to_resid:
                    event_reason = "THRESHOLD"
                elif emit_due_to_hb:
                    event_reason = "HEARTBEAT"
            qv = self._quantizer(kbits).quantize(x_raw).q  # 대표값(실수)
            evt = EventMsg(
                ts=int(ts_ns),
                seq=int(seq),                     # (device_id, sensor, seq)로 de-dup
                device_id=self.cfg.device_id,
                sensor=self.cfg.sensor,
                val=float(qv),
                pred=float(last_pred),
                res=float(resid),
                tau=float(tau),
                kbits=int(kbits),
                profile=self.cfg.profile,
                policy=policy,
                aoi_ms=None,                      # 수집기 기준 계산. 엣지에서는 기록 생략.
                event_reason=event_reason,
            )
            self._last_emit_ns = now_ns
            if emit_due_to_boot:
                self._boot_emitted = True

        # 상태 업데이트(EWMA)
        self._update_ewma(x_raw)
        return evt

    def consume_rate_limit_skips(self) -> int:
        """Return and reset the number of suppressed emissions.

        Args:
            None.

        Returns:
            Count of rate-limited emissions since last call (0 if diagnostics disabled).

        Raises:
            None.

        Side Effects:
            - Resets the internal skip counter.

        Contract:
            - Only increments when diagnostics are enabled.

        Failure Modes:
            - None.
        """
        if not bool(self.cfg.diagnostics_enabled):
            return 0
        n = int(self._rate_limit_skips)
        self._rate_limit_skips = 0
        return n

    def run(
        self, sample_iter: Iterator[Any], duration_s: float | None = None
    ) -> Iterator[EventMsg]:
        """Iterate samples and yield emitted events.

        Args:
            sample_iter: Source iterator of sensor samples.
            duration_s: Optional wall-clock limit in seconds.

        Returns:
            None.

        Yields:
            EventMsg instances for each emission.

        Raises:
            None.

        Side Effects:
            - Updates EWMA state for each sample.

        Contract:
            - Duration is based on wall-clock time, not sample timestamps.

        Failure Modes:
            - Iterator errors propagate to caller.
        """
        end_ns = None if duration_s is None else (time.time_ns() + int(duration_s * 1e9))
        for s in sample_iter:
            if end_ns is not None and time.time_ns() >= end_ns:
                return
            evt = self.predict_and_maybe_emit(s)
            if evt is not None:
                yield evt

    def close(self) -> None:
        """Release predictor resources (no-op for now).

        Args:
            None.

        Returns:
            None.

        Raises:
            None.

        Side Effects:
            - None.

        Contract:
            - Safe to call multiple times.

        Failure Modes:
            - None.
        """
        return

    @property
    def last_emit_ns(self) -> int | None:
        """Timestamp of the most recent emission in ns, or None if none emitted."""
        return self._last_emit_ns

    @property
    def last_pred(self) -> float | None:
        """Most recent EWMA prediction value, or None if uninitialized."""
        return self._last_pred

    # ---------------- 내부 유틸 ----------------

    def _quantizer(self, kbits: int):
        kbits = int(kbits)
        if kbits in self._q_cache:
            return self._q_cache[kbits]
        q = quantizer_for_sensor(self.cfg.sensor, kbits, vmin=self.cfg.vmin, vmax=self.cfg.vmax)
        self._q_cache[kbits] = q
        return q

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
