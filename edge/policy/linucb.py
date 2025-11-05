# edge/policy/linucb.py
# Python 3.10+
# 목적: LinUCB로 (τ, kbits) 팔을 선택하는 적응 정책.
# - 컨텍스트 x: [bias, aoi_norm, res_norm, resvar_norm, loss, qlen_norm]
# - 보상 r = -(α·AoI + β·MAE + γ·Rate) (정규화 후 가중합; 최대화 문제로 변환)
# - 안전가드: AoI_max/MAE_max 위반 시 세이프 팔로 강제 전환(탐색 무시)
# - 결정 로그: PolicyDecisionMsg (reward는 의도적으로 0.0 → 실제 보상은 observe_outcome에서 학습)

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple, List

import math
import time
import numpy as np

from common.schema import PolicyDecisionMsg, PolicyMode, SensorType, LinkProfile


@dataclass(slots=True, frozen=True)
class Arm:
    tau: float
    kbits: int


@dataclass(slots=True)
class LinUCBConfig:
    device_id: str
    sensor: SensorType
    profile: LinkProfile

    # 팔 그리드 (명시하지 않으면 센서별 권장 디폴트 사용; 12~18개 범위)
    arms: Optional[Sequence[Arm]] = None

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

    # 안전가드 임계
    aoi_max_ms: float = 5_000.0      # 5s
    mae_max: float = 2.0             # mic dB / temp °C

    # 워밍업(팔별 최소 시도 횟수 보장)
    warmup_per_arm: int = 1

    # 세이프 팔 (None이면 자동: tau 최소, kbits 최대)
    safe_arm: Optional[Arm] = None


@dataclass(slots=True, frozen=True)
class PolicyState:
    ts_ns: int
    aoi_ms: float
    res: float
    res_var: float
    loss: float       # 0..1
    q_len: int        # >=0


def _default_arms(sensor: SensorType) -> List[Arm]:
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
    """
    LinUCB 컨텍스트 밴딧 정책.
    - 팔별(τ,k)로 A(=λI + Σ x xᵀ), b(=Σ r x) 유지
    - 추정 θ̂ = A⁻¹ b, 선택 점수 p = θ̂ᵀx + α·√(xᵀA⁻¹x)
    - 안전가드: 상태가 임계 초과면 항상 세이프 팔
    - 학습 시점: observe_outcome()에서 직전 결정의 (x, arm)에 보상 r을 적용
    """

    def __init__(self, cfg: LinUCBConfig):
        self.cfg = cfg
        self.arms: List[Arm] = list(cfg.arms) if cfg.arms is not None else _default_arms(cfg.sensor)
        if not self.arms:
            raise ValueError("arms must not be empty")

        # 컨텍스트 차원(d): bias + aoi_norm + res_norm + resvar_norm + loss + qlen_norm
        self.d = 1 + 5
        self._A = [np.eye(self.d, dtype=np.float64) * float(cfg.lambda_ridge) for _ in self.arms]
        self._b = [np.zeros((self.d,), dtype=np.float64) for _ in self.arms]
        self._counts = [0 for _ in self.arms]

        # 세이프 팔 인덱스
        if cfg.safe_arm is None:
            # tau 최소, kbits 최대 조합
            tau_min = min(a.tau for a in self.arms)
            k_max = max(a.kbits for a in self.arms)
            safe_idx = next(i for i, a in enumerate(self.arms) if a.tau == tau_min and a.kbits == k_max)
        else:
            safe_idx = next((i for i, a in enumerate(self.arms)
                             if (abs(a.tau - cfg.safe_arm.tau) < 1e-9 and a.kbits == cfg.safe_arm.kbits)), None)
            if safe_idx is None:
                raise ValueError("safe_arm not found in arms grid")
        self._safe_idx = safe_idx

        # 직전 결정(학습용) 버퍼
        self._last_x: Optional[np.ndarray] = None
        self._last_arm_idx: Optional[int] = None

    # ---------------- 공개 API ----------------

    def decide(self, state: PolicyState) -> Tuple[Tuple[float, int], PolicyDecisionMsg]:
        """
        현재 상태에 대한 (τ,k) 결정을 수행하고 PolicyDecisionMsg를 반환한다.
        - reward 필드는 0.0으로 기록(실제 보상은 observe_outcome에서 적용)
        """
        # 안전가드
        if (state.aoi_ms >= self.cfg.aoi_max_ms) or (abs(state.res) >= self.cfg.mae_max):
            arm_idx = self._safe_idx
        else:
            # 워밍업: 시도 횟수 미달 팔부터 순서대로 사용
            arm_idx = self._select_arm_ucb(state)

        arm = self.arms[arm_idx]
        x = self._context(state)

        # 학습용 버퍼에 기록(직전 결정)
        self._last_x = x
        self._last_arm_idx = arm_idx
        self._counts[arm_idx] += 1

        # 정책 결정 로그(보상은 의도적으로 0.0; 수집기가 실제 r을 계산/분석)
        msg = PolicyDecisionMsg(
            ts=int(state.ts_ns),
            device_id=self.cfg.device_id,
            state_aoi=float(state.aoi_ms),
            state_res=float(state.res),
            state_res_var=float(state.res_var),
            state_loss=float(state.loss),
            state_q_len=int(state.q_len),
            tau=float(arm.tau),
            kbits=int(arm.kbits),
            reward=0.0,
        )
        return (arm.tau, arm.kbits), msg

    def observe_outcome(self, aoi_ms: float, mae: float, rate_bps: float) -> float:
        """
        직전 decide()에 대한 결과(실측 지표)를 받아 LinUCB 파라미터를 업데이트한다.
        반환값: 사용된 보상 r.
        """
        if self._last_x is None or self._last_arm_idx is None:
            # 아직 결정이 없거나 중복 호출
            return 0.0

        # 보상 계산(정규화 후 음의 가중합)
        aoi_n = float(aoi_ms) / max(1e-9, self.cfg.aoi_scale_ms)
        mae_n = float(mae) / max(1e-9, self.cfg.mae_scale)
        rate_n = float(rate_bps) / max(1e-9, self.cfg.rate_scale_bps)
        r = - (self.cfg.w_aoi * aoi_n + self.cfg.w_mae * mae_n + self.cfg.w_rate * rate_n)

        # LinUCB 업데이트
        i = self._last_arm_idx
        x = self._last_x
        A = self._A[i]; b = self._b[i]
        # A ← A + x xᵀ ; b ← b + r x
        A += np.outer(x, x)
        b += r * x
        self._A[i] = A
        self._b[i] = b

        # 버퍼 비움(한 결정-한 업데이트 보장)
        self._last_x = None
        self._last_arm_idx = None
        return float(r)

    def arm_count(self) -> int:
        return len(self.arms)

    def dump_model(self) -> list[dict]:
        """
        팔별 요약(시도 횟수, θ̂, A 대각)을 반환(디버그용).
        """
        out = []
        for i, a in enumerate(self.arms):
            A = self._A[i]
            b = self._b[i]
            try:
                theta = np.linalg.solve(A, b)
            except np.linalg.LinAlgError:
                theta = np.zeros_like(b)
            out.append({
                "arm": {"tau": a.tau, "kbits": a.kbits},
                "counts": self._counts[i],
                "theta": theta.tolist(),
                "A_diag": np.diag(A).tolist(),
            })
        return out

    # ---------------- 내부 ----------------

    def _select_arm_ucb(self, state: PolicyState) -> int:
        x = self._context(state)
        # 워밍업 팔 우선
        for i, c in enumerate(self._counts):
            if c < self.cfg.warmup_per_arm:
                return i

        best_idx = 0
        best_score = -1e100
        for i, (A, b) in enumerate(zip(self._A, self._b)):
            # θ̂ = A⁻¹b (stable: solve)
            try:
                theta = np.linalg.solve(A, b)
            except np.linalg.LinAlgError:
                theta = np.zeros_like(b)
            # 탐색항: s = sqrt(xᵀ A⁻¹ x) → solve(A, x)로 계산
            try:
                Ax = np.linalg.solve(A, x)
                s = float(np.sqrt(max(0.0, float(np.dot(x, Ax)))))
            except np.linalg.LinAlgError:
                s = 0.0
            score = float(np.dot(theta, x) + self.cfg.alpha_ucb * s)
            if score > best_score:
                best_score, best_idx = score, i
        return best_idx

    def _context(self, s: PolicyState) -> np.ndarray:
        # 정규화(0~수 단위 → ~O(1))
        aoi_n = float(s.aoi_ms) / max(1e-9, self.cfg.aoi_scale_ms)
        res_n = float(abs(s.res)) / max(1e-9, self.cfg.res_scale)
        resv_n = float(max(0.0, s.res_var)) / max(1e-9, self.cfg.resvar_scale)
        loss = float(min(1.0, max(0.0, s.loss)))
        qn = float(max(0, s.q_len)) / 50.0  # 보수적 스케일(큐 50개≈1.0)
        x = np.array([1.0, aoi_n, res_n, resv_n, loss, qn], dtype=np.float64)
        return x
