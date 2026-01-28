# edge/policy/runtime.py
# 런타임 정책 오케스트레이터(Periodic / Fixed τ / LinUCB Adaptive)
# - 센서 샘플마다 컨텍스트 계산 → (τ, kbits) 결정 → 이벤트 생성 → 보상 학습
# - 목적: edge_daemon 루프를 단순화하고 LinUCB 학습을 실제 파이프라인에 연결

"""Policy runtime for per-sample decisions and reward attribution.

Bridges prediction, policy selection, and event emission for a single sensor
stream. This module is latency-sensitive and must keep per-sample work bounded
to avoid delaying the sensor loop.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

try:  # resource is not available on all platforms (e.g., Windows)
    import resource  # type: ignore
except Exception:  # pragma: no cover - platform dependent
    resource = None

from common.metrics import OnlineVar
from common.schema import (
    EventMsg,
    LinkProfile,
    PolicyDecisionMsg,
    PolicyMode,
    SensorType,
)
from edge.policy.linucb import Arm, LinUCBConfig, LinUCBPolicy, PolicyState
from edge.predict.ewma import EWMAConfig, EWMAPredictor

__all__ = ["LinkFeedback", "StepResult", "SensorPolicyRuntime", "load_linucb_config"]


@dataclass(slots=True)
class LinkFeedback:
    """Link feedback inputs for policy context (ack latency and loss estimate).

    Args:
        ack_delay_ms: Optional ACK delay estimate in milliseconds.
        loss_rate: Optional loss rate estimate in [0, 1].

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Values are treated as best-effort observations.

    Failure Modes:
        - Missing values are treated as unavailable (None).
    """
    ack_delay_ms: float | None = None
    loss_rate: float | None = None


@dataclass(slots=True)
class StepResult:
    """Outputs from a single policy step, including optional event/decision.

    Args:
        event: Optional EventMsg to enqueue.
        decision: Optional PolicyDecisionMsg (adaptive mode).
        reward: Optional reward value used for learning.
        aoi_ms: AoI estimate in milliseconds.
        mae_est: MAE estimate for the predictor.
        rate_bps: Estimated transmit rate in bits per second.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - `decision` and `reward` are None outside adaptive mode.

    Failure Modes:
        - Values may be NaN when inputs are insufficient.
    """
    event: EventMsg | None
    decision: PolicyDecisionMsg | None
    reward: float | None
    aoi_ms: float
    mae_est: float
    rate_bps: float


class SensorPolicyRuntime:
    """Policy runtime for a single sensor stream.

    Args:
        device_id: Device identifier for EventMsg/PolicyDecisionMsg.
        sensor: Sensor type associated with this runtime.
        profile: Link profile for policy context.
        mode: Policy mode (periodic/fixed/adaptive).
        base_topic: MQTT base topic prefix for event size/rate estimation.
        ewma_cfg: EWMA predictor configuration.
        linucb_cfg: LinUCB configuration when in adaptive mode.
        nominal_period_s: Optional nominal sampling period for AoI fallback.

    Returns:
        None.

    Raises:
        ValueError: If adaptive mode is requested without linucb_cfg.

    Contract:
        - Periodic emits every valid sample.
        - Fixed_tau emits on EWMA residual threshold.
        - Adaptive chooses (tau, kbits) via LinUCB and learns from reward.

    Side Effects:
        - Updates predictor state and LinUCB model in-memory.

    Failure Modes:
        - Invalid samples are skipped but still advance predictor state.
    """

    def __init__(
        self,
        *,
        device_id: str,
        sensor: SensorType,
        profile: LinkProfile,
        mode: PolicyMode,
        base_topic: str = "edge",
        ewma_cfg: EWMAConfig,
        linucb_cfg: LinUCBConfig | None = None,
        nominal_period_s: float | None = None,
    ) -> None:
        self.device_id = device_id
        self.sensor = sensor
        self.profile = profile
        self.mode = mode
        self._base_topic = str(base_topic).strip().strip("/") or "edge"
        if "+" in self._base_topic or "#" in self._base_topic:
            raise ValueError("base_topic must not contain MQTT wildcards '+' or '#'")
        self._predictor = EWMAPredictor(ewma_cfg)
        self._ewma_cfg = ewma_cfg
        self._nominal_period_s = nominal_period_s
        self._res_var = OnlineVar()
        self._last_sent_val: float | None = None
        self._linucb: LinUCBPolicy | None = None
        if mode == PolicyMode.ADAPTIVE:
            if linucb_cfg is None:
                raise ValueError("linucb_cfg is required for adaptive mode")
            self._linucb = LinUCBPolicy(linucb_cfg)

    # ---------------- 공개 API ----------------

    def step(
        self,
        sample: Any,
        outbox_pending: int,
        link_feedback: LinkFeedback | None = None,
    ) -> StepResult:
        """Process one sample and return event/decision outputs.

        Args:
            sample: Sensor sample object from mic/temp sources.
            outbox_pending: Current outbox queue length for congestion context.
            link_feedback: Optional ACK/loss feedback for policy context.

        Returns:
            StepResult with event and/or decision (adaptive only).

        Raises:
            ValueError: If adaptive mode is enabled without a LinUCB config.

        Side Effects:
            - Updates predictor state and LinUCB model parameters.
            - Emits EventMsg when policy conditions are satisfied.

        Contract:
            - Decision/reward are None in non-adaptive modes.
            - Uses ACK delay as an AoI proxy when provided.

        Failure Modes:
            - Invalid sensor samples are ignored for emission but still update state.
        """
        event: EventMsg | None = None
        decision: PolicyDecisionMsg | None = None
        reward: float | None = None
        diag_enabled = bool(self._linucb is not None and self._linucb.cfg.diagnostics_enabled)
        step_wall_start_ns = time.perf_counter_ns() if diag_enabled else 0
        step_cpu_start_ns = time.process_time_ns() if diag_enabled else 0
        t_predict_ms = None
        t_decide_ms = None
        t_observe_ms = None
        ack_delay_ms = 0.0
        loss_est = 0.0
        if link_feedback is not None:
            if link_feedback.ack_delay_ms is not None:
                try:
                    ack_delay_ms = float(link_feedback.ack_delay_ms)
                except Exception:
                    ack_delay_ms = 0.0
                if not math.isfinite(ack_delay_ms) or ack_delay_ms < 0.0:
                    ack_delay_ms = 0.0
            if link_feedback.loss_rate is not None:
                try:
                    loss_est = float(link_feedback.loss_rate)
                except Exception:
                    loss_est = 0.0
                if not math.isfinite(loss_est):
                    loss_est = 0.0
                loss_est = min(1.0, max(0.0, loss_est))

        ts_ns, _seq, x_raw, valid, _last_pred, resid = self._predictor.preview(sample)
        if not valid:
            # sensor invalid sample: update predictor state and return metrics
            self._predictor.predict_and_maybe_emit(sample)
            last_emit_ns = self._predictor.last_emit_ns
            edge_aoi_ms = (
                0.0 if last_emit_ns is None else max(0.0, (ts_ns - last_emit_ns) / 1e6)
            )
            aoi_ms = edge_aoi_ms + ack_delay_ms
            mae_est = self._estimate_mae(x_raw) if math.isfinite(x_raw) else 0.0
            return StepResult(
                event=None,
                decision=None,
                reward=None,
                aoi_ms=float(aoi_ms),
                mae_est=float(mae_est),
                rate_bps=0.0,
            )

        self._res_var.update(resid)
        res_var = self._res_var.var
        if not math.isfinite(res_var):
            res_var = 0.0
        last_emit_ns = self._predictor.last_emit_ns
        edge_aoi_ms = (
            0.0 if last_emit_ns is None else max(0.0, (ts_ns - last_emit_ns) / 1e6)
        )
        aoi_ms = edge_aoi_ms + ack_delay_ms
        q_len = max(0, int(outbox_pending))

        tau = self._ewma_cfg.tau
        kbits = self._ewma_cfg.kbits
        force_emit = False
        force_reason = None
        if self.mode == PolicyMode.ADAPTIVE:
            state = PolicyState(
                ts_ns=int(ts_ns),
                aoi_ms=float(aoi_ms),
                res=float(resid),
                res_var=float(res_var),
                loss=float(loss_est),
                q_len=int(q_len),
            )
            assert self._linucb is not None
            if diag_enabled:
                decide_start_ns = time.perf_counter_ns()
            (tau, kbits), decision = self._linucb.decide(state)
            if diag_enabled:
                t_decide_ms = (time.perf_counter_ns() - decide_start_ns) / 1e6
            cfg = self._linucb.cfg
            if bool(cfg.safety_force_emit_on_aoi) and aoi_ms >= float(cfg.aoi_max_ms):
                force_emit = True
                force_reason = "SAFETY_AOI"
        elif self.mode == PolicyMode.PERIODIC:
            tau = -1e-9  # 항상 전송
            kbits = self._ewma_cfg.kbits

        # 즉시 보상 계산 시점: 전송 여부와 무관하게 현재 MAE/AoI 반영
        mae_est = self._estimate_mae(x_raw)
        rate_bps = 0.0

        if diag_enabled:
            pred_start_ns = time.perf_counter_ns()
        event = self._predictor.predict_and_maybe_emit(
            sample,
            override_tau=tau,
            override_kbits=kbits,
            policy_mode=self.mode,
            force_emit=force_emit,
            force_reason=force_reason,
        )
        if diag_enabled:
            t_predict_ms = (time.perf_counter_ns() - pred_start_ns) / 1e6
        if event is not None:
            # 전송 시점 AoI를 메시지에 기록(선택 필드)
            event = EventMsg(
                ts=event.ts,
                seq=event.seq,
                device_id=event.device_id,
                sensor=event.sensor,
                val=event.val,
                pred=event.pred,
                res=event.res,
                tau=event.tau,
                kbits=event.kbits,
                profile=event.profile,
                policy=event.policy,
                aoi_ms=int(aoi_ms),
                event_reason=event.event_reason,
            )
            rate_bps = self._rate_from_event(event, edge_aoi_ms)
            self._last_sent_val = event.val

        if self._linucb is not None:
            if diag_enabled:
                observe_start_ns = time.perf_counter_ns()
            reward = float(
                self._linucb.observe_outcome(
                    aoi_ms=aoi_ms,
                    mae=mae_est,
                    rate_bps=rate_bps,
                )
            )
            if diag_enabled:
                t_observe_ms = (time.perf_counter_ns() - observe_start_ns) / 1e6
            # 링크 사용량을 줄이기 위해 이벤트 발생 시에만 결정 메시지 송신
            if event is not None and decision is not None:
                diag_enabled = bool(self._linucb.cfg.diagnostics_enabled)
                reward_aoi = None
                reward_mae = None
                reward_rate = None
                rate_limit_skips = None
                t_step_ms = None
                cpu_step_ms = None
                maxrss_kb = None
                if diag_enabled:
                    cfg = self._linucb.cfg
                    aoi_n = float(aoi_ms) / max(1e-9, cfg.aoi_scale_ms)
                    mae_n = float(mae_est) / max(1e-9, cfg.mae_scale)
                    rate_n = float(rate_bps) / max(1e-9, cfg.rate_scale_bps)
                    reward_aoi = float(-(cfg.w_aoi * aoi_n))
                    reward_mae = float(-(cfg.w_mae * mae_n))
                    reward_rate = float(-(cfg.w_rate * rate_n))
                    rate_limit_skips = int(self._predictor.consume_rate_limit_skips())
                    t_step_ms = (time.perf_counter_ns() - step_wall_start_ns) / 1e6
                    cpu_step_ms = (time.process_time_ns() - step_cpu_start_ns) / 1e6
                    if resource is not None:
                        try:
                            maxrss_kb = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
                        except Exception:
                            maxrss_kb = None
                decision = PolicyDecisionMsg(
                    ts=int(ts_ns),
                    device_id=self.device_id,
                    state_aoi=float(aoi_ms),
                    state_res=float(resid),
                    state_res_var=float(res_var),
                    state_loss=float(loss_est),
                    state_q_len=int(q_len),
                    tau=float(tau),
                    kbits=int(kbits),
                    reward=float(reward),
                    arm_id=decision.arm_id,
                    safe_arm_forced=decision.safe_arm_forced,
                    forced_reason=decision.forced_reason,
                    ucb_exploitation=decision.ucb_exploitation,
                    ucb_exploration=decision.ucb_exploration,
                    ucb_score=decision.ucb_score,
                    ucb_alpha=decision.ucb_alpha,
                    reward_aoi=reward_aoi,
                    reward_mae=reward_mae,
                    reward_rate=reward_rate,
                    rate_limit_skips=rate_limit_skips,
                    t_predict_ms=t_predict_ms,
                    t_decide_ms=t_decide_ms,
                    t_observe_ms=t_observe_ms,
                    t_step_ms=t_step_ms,
                    cpu_step_ms=cpu_step_ms,
                    maxrss_kb=maxrss_kb,
                )

        return StepResult(
            event=event,
            decision=decision,
            reward=reward,
            aoi_ms=float(aoi_ms),
            mae_est=float(mae_est),
            rate_bps=float(rate_bps),
        )

    # ---------------- 내부 ----------------

    def _estimate_mae(self, x_raw: float) -> float:
        if self._last_sent_val is None:
            return 0.0
        return float(abs(x_raw - self._last_sent_val))

    def _rate_from_event(self, event: EventMsg, aoi_ms: float) -> float:
        try:
            size_bytes = event.estimated_mqtt_size(qos=1, base_topic=self._base_topic)
        except Exception:
            size_bytes = len(event.to_json_bytes())
        interval_s = aoi_ms / 1000.0 if aoi_ms > 0 else None
        if interval_s is None or interval_s <= 0:
            interval_s = self._nominal_period_s or 1.0
        interval_s = max(0.05, interval_s)
        return float(size_bytes * 8.0 / interval_s)


def load_linucb_config(
    cfg_dict: dict,
    *,
    device_id: str,
    sensor: SensorType,
    profile: LinkProfile,
    seed: int | None = None,
    mae_scale: float | None = None,
    res_scale: float | None = None,
    resvar_scale: float | None = None,
) -> LinUCBConfig:
    """Build LinUCBConfig from a YAML/JSON mapping.

    Args:
        cfg_dict: Policy config mapping (configs/policy*.yaml schema).
        device_id: Device identifier for logging/telemetry.
        sensor: Sensor type to select arms/scales.
        profile: Link profile label.
        seed: Optional RNG seed for reproducibility.
        mae_scale: Optional MAE scale override derived from arms.
        res_scale: Optional residual scale override derived from arms.
        resvar_scale: Optional residual variance scale override derived from arms.

    Returns:
        LinUCBConfig populated with parsed arms/reward/safety/scales.

    Raises:
        KeyError: If arms entries are missing required keys.
        ValueError: If numeric fields cannot be converted.

    Side Effects:
        - None.

    Contract:
        - Expects `arms` entries to provide tau/kbits.

    Failure Modes:
        - Bad YAML values surface as conversion errors to caller.
    """
    arms_raw = cfg_dict.get("arms") or []
    arms = [Arm(tau=float(a["tau"]), kbits=int(a["kbits"])) for a in arms_raw]
    reward = cfg_dict.get("reward", {}) or {}
    safety = cfg_dict.get("safety", {}) or {}
    diagnostics = cfg_dict.get("diagnostics", {}) or {}
    scales = cfg_dict.get("scales", {}) or {}
    return LinUCBConfig(
        device_id=device_id,
        sensor=sensor,
        profile=profile,
        seed=int(seed) if seed is not None else None,
        arms=arms,
        w_aoi=float(reward.get("alpha", 1.0)),
        w_mae=float(reward.get("beta", 1.0)),
        w_rate=float(reward.get("gamma", 1.0)),
        aoi_scale_ms=float(scales.get("aoi_ms", 1000.0)),
        rate_scale_bps=float(scales.get("rate_bps", 1024.0)),
        aoi_max_ms=float(safety.get("aoi_max_ms", 5000.0)),
        mae_max=float(safety.get("mae_max", 2.0)),
        safety_force_emit_on_aoi=bool(safety.get("safety_force_emit_on_aoi", False)),
        mae_scale=float(mae_scale) if mae_scale is not None else 1.0,
        res_scale=float(res_scale) if res_scale is not None else 1.0,
        resvar_scale=float(resvar_scale) if resvar_scale is not None else 1.0,
        diagnostics_enabled=bool(diagnostics.get("enabled", False)),
    )
