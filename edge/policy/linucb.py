# edge/policy/linucb.py
# Python 3.10+
# 목적: LinUCB로 (τ, kbits) 팔을 선택하는 적응 정책.
# - 컨텍스트 x: [bias, aoi_norm, res_norm, resvar_norm, loss, qlen_norm]
# - 보상 r = -(α·AoI + β·MAE + γ·Rate) (정규화 후 가중합; 최대화 문제로 변환)
# - 안전가드: AoI_max/MAE_max 위반 시 세이프 팔로 강제 전환(탐색 무시)
# - 결정 로그: PolicyDecisionMsg (reward는 의도적으로 0.0 → 실제 보상은 observe_outcome에서 학습)

"""LinUCB policy for selecting (tau, kbits) arms under link constraints.

Implements a contextual bandit with ridge regression and UCB exploration.
Safety guards can override arm choice to enforce AoI/MAE limits.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from common.schema import LinkProfile, PolicyDecisionMsg, SensorType

logger = logging.getLogger(__name__)

__all__ = [
    "Arm",
    "LinUCBConfig",
    "PolicyState",
    "LinUCBPolicy",
    "LinUCB",
]


@dataclass(slots=True, frozen=True)
class Arm:
    """Immutable arm definition for (tau, kbits) selection.

    Args:
        tau: Sampling threshold or interval for the arm.
        kbits: Quantization bit width (1..16).

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Used as a value object; no validation beyond type coercion.

    Failure Modes:
        - Invalid values surface when the policy validates arms.
    """
    tau: float
    kbits: int


@dataclass(slots=True)
class LinUCBConfig:
    """Configuration for LinUCB policy behavior and constraints.

    Args:
        device_id: Device identifier for logging.
        sensor: Sensor type for arm defaults.
        profile: Link profile identifier.
        seed: Optional RNG seed for deterministic choices.
        arms: Optional explicit arm list; defaults to sensor-specific grid.
        alpha_ucb: UCB exploration strength (alpha).
        lambda_ridge: Ridge regularization coefficient.
        w_aoi: AoI weight in reward.
        w_mae: MAE weight in reward.
        w_rate: Rate weight in reward.
        aoi_scale_ms: AoI normalization scale in ms.
        mae_scale: MAE normalization scale.
        rate_scale_bps: Rate normalization scale in bps.
        res_scale: Residual normalization scale.
        resvar_scale: Residual variance normalization scale.
        aoi_max_ms: AoI safety threshold in ms.
        mae_max: MAE safety threshold.
        mae_est_max: Optional MAE(est) threshold for reward/penalties.
        safety_force_emit_on_aoi: Whether to force emit when AoI exceeds limit.
        warmup_per_arm: Minimum pulls per arm before UCB selection.
        ucb_min_tau: Optional minimum tau for UCB selection.
        safe_arm: Optional explicit safe arm override.
        aoi_safe_arm: Optional safe arm override for AoI-limit forcing.
        diagnostics_enabled: Emit diagnostic scalars when True.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Values are consumed by LinUCBPolicy without additional validation.

    Failure Modes:
        - Invalid values surface during policy initialization.
    """
    device_id: str
    sensor: SensorType
    profile: LinkProfile
    seed: int | None = None

    # 팔 그리드 (명시하지 않으면 센서별 권장 디폴트 사용; 12~18개 범위)
    arms: Sequence[Arm] | None = None

    # LinUCB 하이퍼파라미터
    alpha_ucb: float = 0.75     # 탐색 강도(Confidence width)
    lambda_ridge: float = 1.0   # 릿지(정규화) λ

    # 보상 가중치 r = -(α·AoI + β·MAE + γ·Rate)
    w_aoi: float = 1.0
    w_mae: float = 1.0
    w_rate: float = 1.0

    # 상태 정규화 스케일(컨텍스트와 보상 모두에 사용)
    aoi_scale_ms: float = 1000.0     # 1초 기준
    mae_scale: float = 1.0           # mic: dB, temp: °C (센서별 값 권장)
    rate_scale_bps: float = 1024.0   # 1 KB/s 기준
    res_scale: float = 1.0           # 잔차(dB/°C)
    resvar_scale: float = 1.0        # 잔차분산
    q_len_scale: float = 50.0        # outbox pending count 정규화 스케일

    # 안전가드 임계
    aoi_max_ms: float = 5_000.0      # 5s
    # NOTE: `mae_max` historically refers to the EWMA residual (|x - pred|) threshold used as a
    # safety guard in `decide()`. If you want a *different* threshold for `mae` passed into
    # observe_outcome (which is `|x - last_sent|` in the runtime), set `mae_est_max`.
    #
    # For PoC/research, forcing a "safe arm" on moderate residuals can hide the effect of LinUCB.
    # Use `residual_guard_enabled=false` (or a very large `mae_max`) to evaluate the policy
    # without hard overrides.
    residual_guard_enabled: bool = True
    mae_max: float = 2.0             # mic dB / temp °C (residual threshold for safety)
    mae_est_max: float | None = None # optional: staleness MAE threshold for reward shaping
    safety_force_emit_on_aoi: bool = False
    coverage_force_emit_on_unhit_segment: bool = False

    # 워밍업(팔별 최소 시도 횟수 보장)
    warmup_per_arm: int = 1

    # Optional constraint on the action set for non-safety selection.
    # When set, arms with tau < ucb_min_tau are excluded from UCB selection unless a safety
    # override is active (e.g., AoI/MAE limit). This helps prevent "oversending" from
    # overly aggressive arms while still keeping a safe arm for anomalies.
    ucb_min_tau: float | None = None

    # 세이프 팔 (None이면 자동: tau 최소, kbits 최대)
    safe_arm: Arm | None = None

    # Optional separate safe arm used when AoI limit triggers (without MAE/residual limit).
    # Useful to avoid selecting an overly aggressive (low tau) arm just because AoI is high.
    aoi_safe_arm: Arm | None = None

    # Telemetry (default: off). When enabled, policy emits lightweight diagnostics scalars.
    diagnostics_enabled: bool = False

    # Reward shaping for KPI4 (segment recall), implemented in the runtime as a MAE-overage term.
    # These are *not* hard guardrails: they do not force arms, but bias learning toward
    # "emit once per anomaly segment" behavior even when segments are short.
    coverage_step_penalty_n: float = 1.0
    coverage_miss_penalty_n: float = 0.0


@dataclass(slots=True, frozen=True)
class PolicyState:
    """Context features used by LinUCB to choose an arm.

    Args:
        ts_ns: Timestamp for the state in nanoseconds.
        aoi_ms: AoI estimate in milliseconds.
        res: Residual error estimate.
        res_var: Residual variance estimate.
        loss: Loss estimate in [0, 1].
        q_len: Queue length estimate (>= 0).
        seg_active: Optional anomaly-segment flag (0 or 1).
        seg_unhit: Optional anomaly-segment "needs hit" flag (0 or 1).

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Values are normalized inside the policy before scoring.

    Failure Modes:
        - Non-finite values are sanitized during decision making.
    """
    ts_ns: int
    aoi_ms: float
    res: float
    res_var: float
    loss: float       # 0..1
    q_len: int        # >=0
    seg_active: float = 0.0  # 0/1 (segment marker)
    seg_unhit: float = 0.0  # 0/1 (reward-shaping hint)


def _default_arms(sensor: SensorType) -> list[Arm]:
    if sensor == SensorType.MIC_RMS:
        # 4×3=12개: τ∈{1.5,2.5,3.5,4.5} dB, k∈{4,6,8}
        taus = [1.5, 2.5, 3.5, 4.5]
        ks = [4, 6, 8]
    elif sensor == SensorType.TEMP:
        # 4×3=12개: τ∈{0.05,0.1,0.2,0.3} °C, k∈{6,8,10}
        taus = [0.05, 0.1, 0.2, 0.3]
        ks = [6, 8, 10]
    else:
        # 기본 안전: 소폭 τ와 중간 k
        taus = [0.5, 1.0, 2.0]
        ks = [6, 8]
    return [Arm(tau=t, kbits=k) for t in taus for k in ks]


class LinUCBPolicy:
    """Contextual bandit policy using LinUCB.

    Args:
        cfg: LinUCBConfig with arm grid, scaling, and safety limits.

    Returns:
        None.

    Raises:
        ValueError: If the arm grid is empty or the safe arm is missing.

    Contract:
        - Maintains per-arm ridge regression state (A, b) and counts.
        - `decide` must be followed by `observe_outcome` to update the chosen arm.
        - Safety guardrails can override exploration.

    Side Effects:
        - Updates in-memory model parameters and internal counters.

    Failure Modes:
        - Invalid states are sanitized and logged; selection continues.
    """

    def __init__(self, cfg: LinUCBConfig):
        self.cfg = cfg
        if cfg.seed is not None:
            logger.info(
                "linucb_seed device_id=%s sensor=%s seed=%s",
                cfg.device_id,
                cfg.sensor.value,
                int(cfg.seed),
            )
        self.arms: list[Arm] = list(cfg.arms) if cfg.arms is not None else _default_arms(cfg.sensor)
        if not self.arms:
            raise ValueError("arms must not be empty")

        # 컨텍스트 차원(d): bias + aoi_norm + res_norm + resvar_norm + loss + qlen_norm + seg_active + seg_unhit
        self.d = 1 + 7
        self._A = [np.eye(self.d, dtype=np.float64) * float(cfg.lambda_ridge) for _ in self.arms]
        self._b = [np.zeros((self.d,), dtype=np.float64) for _ in self.arms]
        self._counts = [0 for _ in self.arms]

        # 세이프 팔 인덱스
        if cfg.safe_arm is None:
            # Conservative default that works even when `arms` is not a full grid:
            # pick the smallest tau; if multiple exist, pick the largest kbits among them.
            safe_idx = min(
                range(len(self.arms)),
                key=lambda i: (float(self.arms[i].tau), -int(self.arms[i].kbits)),
            )
        else:
            safe_idx = next(
                (
                    i
                    for i, a in enumerate(self.arms)
                    if (
                        abs(a.tau - cfg.safe_arm.tau) < 1e-9
                        and a.kbits == cfg.safe_arm.kbits
                    )
                ),
                None,
            )
            if safe_idx is None:
                raise ValueError("safe_arm not found in arms grid")
        self._safe_idx = int(safe_idx)

        # Optional separate safe arm used when AoI-limit forcing is active.
        # This prevents AoI guardrail interventions from skewing the MAE/coverage safe arm.
        if cfg.aoi_safe_arm is None:
            self._aoi_safe_idx = int(self._safe_idx)
        else:
            aoi_safe_idx = next(
                (
                    i
                    for i, a in enumerate(self.arms)
                    if (
                        abs(a.tau - cfg.aoi_safe_arm.tau) < 1e-9
                        and a.kbits == cfg.aoi_safe_arm.kbits
                    )
                ),
                None,
            )
            if aoi_safe_idx is None:
                raise ValueError("aoi_safe_arm not found in arms grid")
            self._aoi_safe_idx = int(aoi_safe_idx)

        # 직전 결정(학습용) 버퍼
        self._last_x: np.ndarray | None = None
        self._last_arm_idx: int | None = None
        self._last_logged_arm_idx: int | None = None

    # ---------------- 공개 API ----------------

    def decide(self, state: PolicyState) -> tuple[tuple[float, int], PolicyDecisionMsg]:
        """Choose an arm for the current state and emit a decision message.

        Args:
            state: Current policy context (AoI/residual/loss/queue length).

        Returns:
            ((tau, kbits), PolicyDecisionMsg) for logging and actuation.

        Raises:
            ValueError: If configuration yields no valid arms.

        Side Effects:
            - Updates internal buffers for the subsequent `observe_outcome`.

        Contract:
            - `reward` in the decision message is a placeholder (0.0).
            - Sanitizes non-finite state values rather than aborting.

        Failure Modes:
            - If safe arm is missing from the grid, initialization fails early.
        """
        invalid_state = False
        aoi_ms = float(state.aoi_ms)
        res = float(state.res)
        res_var = float(state.res_var)
        loss = float(state.loss)
        q_len = int(state.q_len)
        seg_active = float(getattr(state, "seg_active", 0.0))
        seg_unhit = float(getattr(state, "seg_unhit", 0.0))
        if not math.isfinite(aoi_ms):
            aoi_ms = 0.0
            invalid_state = True
        if not math.isfinite(res):
            res = 0.0
            invalid_state = True
        if not math.isfinite(res_var) or res_var < 0.0:
            res_var = 0.0
            invalid_state = True
        if not math.isfinite(loss):
            loss = 0.0
            invalid_state = True
        if q_len < 0:
            q_len = 0
            invalid_state = True
        if not math.isfinite(seg_active):
            seg_active = 0.0
            invalid_state = True
        if not math.isfinite(seg_unhit):
            seg_unhit = 0.0
            invalid_state = True
        seg_active = float(min(1.0, max(0.0, seg_active)))
        seg_unhit = float(min(1.0, max(0.0, seg_unhit)))
        if invalid_state:
            logger.warning(
                "linucb_state_sanitized device_id=%s sensor=%s",
                self.cfg.device_id,
                self.cfg.sensor.value,
            )

        safe_state = PolicyState(
            ts_ns=int(state.ts_ns),
            aoi_ms=float(aoi_ms),
            res=float(res),
            res_var=float(res_var),
            loss=float(loss),
            q_len=int(q_len),
            seg_active=float(seg_active),
            seg_unhit=float(seg_unhit),
        )

        # AoI is a hard guardrail only when configured to force-emit.
        aoi_limit = bool(self.cfg.safety_force_emit_on_aoi and aoi_ms >= self.cfg.aoi_max_ms)
        mae_limit = bool(self.cfg.residual_guard_enabled and abs(res) >= self.cfg.mae_max)
        safe_forced = bool(aoi_limit or mae_limit)

        # 안전가드
        if safe_forced:
            if aoi_limit and (not mae_limit):
                arm_idx = self._aoi_safe_idx
            else:
                arm_idx = self._safe_idx
        else:
            # 워밍업: 시도 횟수 미달 팔부터 순서대로 사용
            arm_idx = self._select_arm_ucb(safe_state)

        arm = self.arms[arm_idx]
        if arm_idx != self._last_logged_arm_idx:
            forced_reason = "NONE"
            if safe_forced:
                if aoi_limit and mae_limit:
                    forced_reason = "BOTH"
                elif aoi_limit:
                    forced_reason = "AOI_LIMIT"
                else:
                    forced_reason = "MAE_LIMIT"
            logger.info(
                "linucb_arm_select device_id=%s sensor=%s profile=%s arm_id=%d tau=%.6g kbits=%d "
                "safe_arm_forced=%s forced_reason=%s",
                self.cfg.device_id,
                self.cfg.sensor.value,
                self.cfg.profile.value,
                int(arm_idx),
                float(arm.tau),
                int(arm.kbits),
                bool(safe_forced),
                forced_reason,
            )
            self._last_logged_arm_idx = arm_idx
        x = self._context(safe_state)

        diag: dict[str, object] = {}
        if bool(self.cfg.diagnostics_enabled):
            forced_reason = "NONE"
            if safe_forced:
                if aoi_limit and mae_limit:
                    forced_reason = "BOTH"
                elif aoi_limit:
                    forced_reason = "AOI_LIMIT"
                else:
                    forced_reason = "MAE_LIMIT"

            a_mat = self._A[arm_idx]
            b_vec = self._b[arm_idx]
            try:
                theta = np.linalg.solve(a_mat, b_vec)
            except np.linalg.LinAlgError:
                theta = np.zeros_like(b_vec)
            exploitation = float(np.dot(theta, x))
            try:
                a_x = np.linalg.solve(a_mat, x)
                uncertainty = float(np.sqrt(max(0.0, float(np.dot(x, a_x)))))
            except np.linalg.LinAlgError:
                uncertainty = 0.0
            exploration = float(self.cfg.alpha_ucb * uncertainty)
            score = float(exploitation + exploration)

            diag = {
                "arm_id": int(arm_idx),
                "safe_arm_forced": bool(safe_forced),
                "forced_reason": str(forced_reason),
                "ucb_exploitation": float(exploitation),
                "ucb_exploration": float(exploration),
                "ucb_score": float(score),
                # Store alpha to allow deriving uncertainty in analysis: u = exploration / alpha
                "ucb_alpha": float(self.cfg.alpha_ucb),
            }

        # 학습용 버퍼에 기록(직전 결정)
        if invalid_state:
            self._last_x = None
            self._last_arm_idx = None
        else:
            self._last_x = x
            self._last_arm_idx = arm_idx
        self._counts[arm_idx] += 1

        # 정책 결정 로그(보상은 의도적으로 0.0; 수집기가 실제 r을 계산/분석)
        msg = PolicyDecisionMsg(
            ts=int(safe_state.ts_ns),
            device_id=self.cfg.device_id,
            state_aoi=float(aoi_ms),
            state_res=float(res),
            state_res_var=float(res_var),
            state_loss=float(loss),
            state_q_len=int(q_len),
            tau=float(arm.tau),
            kbits=int(arm.kbits),
            reward=0.0,
            **diag,
        )
        return (arm.tau, arm.kbits), msg

    def observe_outcome(self, aoi_ms: float, mae: float, rate_bps: float) -> float:
        """Apply reward for the most recent decision and update model state.

        Args:
            aoi_ms: Observed AoI in milliseconds.
            mae: Observed event MAE estimate.
            rate_bps: Observed rate in bits per second.

        Returns:
            The computed reward value (negative weighted sum).

        Raises:
            None.

        Side Effects:
            - Updates A/b matrices and internal counts for the chosen arm.

        Contract:
            - Should be called once per `decide` to keep learning consistent.

        Failure Modes:
            - Non-finite inputs cause the update to be skipped and return 0.0.
        """
        if self._last_x is None or self._last_arm_idx is None:
            # 아직 결정이 없거나 중복 호출
            return 0.0
        if not np.isfinite(self._last_x).all():
            logger.warning(
                "linucb_skip_update invalid_context device_id=%s sensor=%s",
                self.cfg.device_id,
                self.cfg.sensor.value,
            )
            self._last_x = None
            self._last_arm_idx = None
            return 0.0
        if not (math.isfinite(aoi_ms) and math.isfinite(mae) and math.isfinite(rate_bps)):
            logger.warning(
                "linucb_skip_update nonfinite_reward_inputs device_id=%s sensor=%s",
                self.cfg.device_id,
                self.cfg.sensor.value,
            )
            self._last_x = None
            self._last_arm_idx = None
            return 0.0

        # Reward shaping (constraint-style):
        # - Always penalize transmit rate (rate_bps).
        # - Penalize AoI/MAE only when they exceed configured guardrails.
        #
        # This better matches the project's KPI framing where efficiency is primary,
        # and freshness/quality are guardrails rather than continuously optimized.
        aoi_scale = max(1e-9, float(self.cfg.aoi_scale_ms))
        mae_scale = max(1e-9, float(self.cfg.mae_scale))
        rate_scale = max(1e-9, float(self.cfg.rate_scale_bps))

        aoi_max = float(self.cfg.aoi_max_ms)
        if not math.isfinite(aoi_max) or aoi_max < 0.0:
            aoi_max = 0.0
        mae_max = self.cfg.mae_est_max
        if mae_max is None or not math.isfinite(float(mae_max)) or float(mae_max) < 0.0:
            mae_max = float(self.cfg.mae_max)
        mae_max = float(mae_max)

        aoi_over_n = max(0.0, float(aoi_ms) - aoi_max) / aoi_scale
        mae_over_n = max(0.0, float(mae) - mae_max) / mae_scale
        rate_n = float(rate_bps) / rate_scale
        r = -(
            self.cfg.w_rate * rate_n
            + self.cfg.w_aoi * aoi_over_n
            + self.cfg.w_mae * mae_over_n
        )

        # LinUCB 업데이트
        i = self._last_arm_idx
        x = self._last_x
        a_mat = self._A[i]
        b_vec = self._b[i]
        # A ← A + x xᵀ ; b ← b + r x
        a_mat += np.outer(x, x)
        b_vec += r * x
        self._A[i] = a_mat
        self._b[i] = b_vec

        # 버퍼 비움(한 결정-한 업데이트 보장)
        self._last_x = None
        self._last_arm_idx = None
        return float(r)

    def arm_count(self) -> int:
        """Return the number of configured arms.

        Args:
            None.

        Returns:
            Count of arms in the current grid.

        Raises:
            None.

        Side Effects:
            - None.

        Contract:
            - Reflects the current arm list stored on the policy.

        Failure Modes:
            - None.
        """
        return len(self.arms)

    def dump_model(self) -> list[dict]:
        """Return per-arm diagnostic summaries for debugging.

        Args:
            None.

        Returns:
            List of dicts with arm parameters, counts, and model summaries.

        Raises:
            None.

        Side Effects:
            - None.

        Contract:
            - Uses the current in-memory model state.

        Failure Modes:
            - Linear algebra failures yield zero-valued parameter estimates.
        """
        out = []
        for i, a in enumerate(self.arms):
            a_mat = self._A[i]
            b_vec = self._b[i]
            try:
                theta = np.linalg.solve(a_mat, b_vec)
            except np.linalg.LinAlgError:
                theta = np.zeros_like(b_vec)
            out.append({
                "arm": {"tau": a.tau, "kbits": a.kbits},
                "counts": self._counts[i],
                "theta": theta.tolist(),
                "A_diag": np.diag(a_mat).tolist(),
            })
        return out

    # ---------------- 내부 ----------------

    def _select_arm_ucb(self, state: PolicyState) -> int:
        x = self._context(state)

        # Optionally constrain the candidate set (exclude overly aggressive tau values).
        cand: list[int]
        min_tau = self.cfg.ucb_min_tau
        if min_tau is None or not math.isfinite(float(min_tau)):
            cand = list(range(len(self.arms)))
        else:
            thr = float(min_tau)
            cand = [i for i, a in enumerate(self.arms) if float(a.tau) >= thr]
            if not cand:
                return int(self._safe_idx)

        # 워밍업 팔 우선 (candidate set only)
        for i in cand:
            if int(self._counts[i]) < int(self.cfg.warmup_per_arm):
                return int(i)

        best_idx = int(cand[0])
        best_score = -1e100
        for i in cand:
            a_mat = self._A[i]
            b_vec = self._b[i]
            # θ̂ = A⁻¹b (stable: solve)
            try:
                theta = np.linalg.solve(a_mat, b_vec)
            except np.linalg.LinAlgError:
                theta = np.zeros_like(b_vec)
            # 탐색항: s = sqrt(xᵀ A⁻¹ x) → solve(A, x)로 계산
            try:
                a_x = np.linalg.solve(a_mat, x)
                s = float(np.sqrt(max(0.0, float(np.dot(x, a_x)))))
            except np.linalg.LinAlgError:
                s = 0.0
            score = float(np.dot(theta, x) + self.cfg.alpha_ucb * s)
            if score > best_score:
                best_score, best_idx = score, i
        return int(best_idx)

    def _context(self, s: PolicyState) -> np.ndarray:
        # 정규화(0~수 단위 → ~O(1))
        aoi_n = float(s.aoi_ms) / max(1e-9, self.cfg.aoi_scale_ms)
        res_n = float(abs(s.res)) / max(1e-9, self.cfg.res_scale)
        resv_n = float(max(0.0, s.res_var)) / max(1e-9, self.cfg.resvar_scale)
        loss = float(min(1.0, max(0.0, s.loss)))
        # 큐 길이는 링크 불안정 시 수십~수천까지 갈 수 있으므로 log 스케일을 사용.
        q = float(max(0, s.q_len))
        q_scale = float(self.cfg.q_len_scale)
        if not math.isfinite(q_scale) or q_scale <= 1.0:
            q_scale = 50.0
        qn = float(math.log1p(q) / math.log1p(q_scale))
        seg_active = float(min(1.0, max(0.0, getattr(s, "seg_active", 0.0))))
        seg = float(min(1.0, max(0.0, getattr(s, "seg_unhit", 0.0))))
        x = np.array([1.0, aoi_n, res_n, resv_n, loss, qn, seg_active, seg], dtype=np.float64)
        return x


class LinUCB:
    """Backwards-compatible LinUCB variant for the lightweight unit tests.

    Args:
        arms: Arm list as (tau, kbits) tuples or Arm objects.
        d: Context feature dimension.
        alpha: UCB exploration strength.
        lambda_ridge: Ridge regularization coefficient.

    Returns:
        None.

    Raises:
        ValueError: If d/alpha are invalid or arms are empty.

    Side Effects:
        - Updates in-memory model parameters when `update` is called.

    Contract:
        - Context vectors must have shape (d,).

    Failure Modes:
        - Invalid contexts raise ValueError.
    """

    def __init__(
        self,
        arms: Sequence[tuple[float, int]] | Sequence[Arm],
        d: int,
        alpha: float = 0.7,
        lambda_ridge: float = 1.0,
    ) -> None:
        if d <= 0:
            raise ValueError("d must be positive")
        if alpha <= 0:
            raise ValueError("alpha must be positive")

        self.arms: list[Arm] = [a if isinstance(a, Arm) else Arm(*a) for a in arms]
        if not self.arms:
            raise ValueError("arms must not be empty")

        self.d = int(d)
        self.alpha = float(alpha)
        self.lambda_ridge = float(lambda_ridge)

        self._A = [np.eye(self.d, dtype=np.float64) * self.lambda_ridge for _ in self.arms]
        self._b = [np.zeros((self.d,), dtype=np.float64) for _ in self.arms]

    def select(self, context: Sequence[float] | np.ndarray) -> tuple[float, int]:
        x = np.asarray(context, dtype=np.float64)
        if x.shape != (self.d,):
            raise ValueError(f"context must have shape ({self.d},)")

        best_idx = 0
        best_score = -math.inf
        for i, (a_mat, b_vec) in enumerate(zip(self._A, self._b)):
            try:
                theta = np.linalg.solve(a_mat, b_vec)
            except np.linalg.LinAlgError:
                theta = np.zeros_like(b_vec)
            try:
                a_x = np.linalg.solve(a_mat, x)
                s = float(np.sqrt(max(0.0, float(np.dot(x, a_x)))))
            except np.linalg.LinAlgError:
                s = 0.0
            score = float(np.dot(theta, x) + self.alpha * s)
            if score > best_score:
                best_score = score
                best_idx = i
        arm = self.arms[best_idx]
        return (arm.tau, arm.kbits)

    def update(
        self,
        arm: tuple[float, int] | Arm,
        reward: float,
        context: Sequence[float] | np.ndarray,
    ) -> None:
        x = np.asarray(context, dtype=np.float64)
        if x.shape != (self.d,):
            raise ValueError(f"context must have shape ({self.d},)")

        if not isinstance(arm, Arm):
            arm = Arm(*arm)
        try:
            idx = next(i for i, a in enumerate(self.arms) if a == arm)
        except StopIteration as exc:  # pragma: no cover - defensive branch
            raise ValueError("arm not part of the policy") from exc

        self._A[idx] += np.outer(x, x)
        self._b[idx] += float(reward) * x
