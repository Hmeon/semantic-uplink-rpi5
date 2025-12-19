# edge/policy/runtime.py
# 런타임 정책 오케스트레이터(Periodic / Fixed τ / LinUCB Adaptive)
# - 센서 샘플마다 컨텍스트 계산 → (τ, kbits) 결정 → 이벤트 생성 → 보상 학습
# - 목적: edge_daemon 루프를 단순화하고 LinUCB 학습을 실제 파이프라인에 연결

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

__all__ = ["StepResult", "SensorPolicyRuntime", "load_linucb_config"]


@dataclass(slots=True)
class StepResult:
    event: EventMsg | None
    decision: PolicyDecisionMsg | None
    reward: float | None
    aoi_ms: float
    mae_est: float
    rate_bps: float


class SensorPolicyRuntime:
    """
    센서 1개에 대한 정책 실행기.
    - Periodic: 모든 샘플 전송
    - Fixed τ: EWMA+임계값 SoD
    - Adaptive: LinUCB로 (τ, kbits) 선택 → 보상으로 즉시 학습
    """

    def __init__(
        self,
        *,
        device_id: str,
        sensor: SensorType,
        profile: LinkProfile,
        mode: PolicyMode,
        ewma_cfg: EWMAConfig,
        linucb_cfg: LinUCBConfig | None = None,
        nominal_period_s: float | None = None,
    ) -> None:
        self.device_id = device_id
        self.sensor = sensor
        self.profile = profile
        self.mode = mode
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

    def step(self, sample: Any, outbox_pending: int) -> StepResult:
        """
        샘플 1개 처리 → (EventMsg?, PolicyDecisionMsg?, reward?)
        - adaptive 모드가 아닌 경우 decision/reward는 None
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

        ts_ns, _seq, x_raw, valid, _last_pred, resid = self._predictor.preview(sample)
        if not valid:
            # 센서 오류 시 상태만 갱신하고 종료
            self._predictor.predict_and_maybe_emit(sample)
            last_emit_ns = self._predictor.last_emit_ns
            aoi_ms = 0.0 if last_emit_ns is None else max(0.0, (ts_ns - last_emit_ns) / 1e6)
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
        aoi_ms = (
            0.0 if last_emit_ns is None else max(0.0, (ts_ns - last_emit_ns) / 1e6)
        )
        loss_est = 0.0  # 링크 손실 측정치 없음 → 0 가정(collector에서 실제 계산)
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
            rate_bps = self._rate_from_event(event, aoi_ms)
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
            size_bytes = event.estimated_mqtt_size(qos=1)
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
    """
    YAML/JSON dict → LinUCBConfig 변환.
    - cfg_dict는 configs/policy.yaml 스키마를 기대.
    """
    arms_raw = cfg_dict.get("arms") or []
    arms = [Arm(tau=float(a["tau"]), kbits=int(a["kbits"])) for a in arms_raw]
    reward = cfg_dict.get("reward", {}) or {}
    safety = cfg_dict.get("safety", {}) or {}
    diagnostics = cfg_dict.get("diagnostics", {}) or {}
    return LinUCBConfig(
        device_id=device_id,
        sensor=sensor,
        profile=profile,
        seed=int(seed) if seed is not None else None,
        arms=arms,
        w_aoi=float(reward.get("alpha", 1.0)),
        w_mae=float(reward.get("beta", 1.0)),
        w_rate=float(reward.get("gamma", 1.0)),
        aoi_max_ms=float(safety.get("aoi_max_ms", 5000.0)),
        mae_max=float(safety.get("mae_max", 2.0)),
        safety_force_emit_on_aoi=bool(safety.get("safety_force_emit_on_aoi", False)),
        mae_scale=float(mae_scale) if mae_scale is not None else 1.0,
        res_scale=float(res_scale) if res_scale is not None else 1.0,
        resvar_scale=float(resvar_scale) if resvar_scale is not None else 1.0,
        diagnostics_enabled=bool(diagnostics.get("enabled", False)),
    )
