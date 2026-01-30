from __future__ import annotations

import math

import numpy as np
import pytest

from common.schema import LinkProfile, SensorType
from edge.policy.linucb import Arm, LinUCBConfig, LinUCBPolicy, PolicyState


def _make_state(*, aoi_ms: float = 0.0, res: float = 0.1) -> PolicyState:
    return PolicyState(
        ts_ns=1,
        aoi_ms=aoi_ms,
        res=res,
        res_var=0.0,
        loss=0.0,
        q_len=0,
    )


def test_linucb_warmup_per_arm_selection_order() -> None:
    arms = [Arm(0.1, 6), Arm(0.2, 8), Arm(0.3, 10)]
    cfg = LinUCBConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        arms=arms,
        warmup_per_arm=2,
        aoi_max_ms=1e9,
        mae_max=1e9,
        diagnostics_enabled=True,
    )
    pol = LinUCBPolicy(cfg)
    seq = []
    for _ in range(6):
        _, decision = pol.decide(_make_state())
        seq.append(decision.arm_id)
    assert seq == [0, 0, 1, 1, 2, 2]


def test_linucb_ucb_scoring_components() -> None:
    arms = [Arm(0.1, 6)]
    cfg = LinUCBConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        arms=arms,
        alpha_ucb=0.5,
        warmup_per_arm=0,
        aoi_max_ms=1e9,
        mae_max=1e9,
        diagnostics_enabled=True,
    )
    pol = LinUCBPolicy(cfg)
    pol._A[0] = np.eye(pol.d, dtype=np.float64)
    pol._b[0] = np.array([1, 2, 3, 4, 5, 6, 7, 8], dtype=np.float64)
    state = _make_state(aoi_ms=1000.0, res=1.0)
    _, decision = pol.decide(state)

    x = pol._context(state)
    exploitation = float(np.dot(pol._b[0], x))
    uncertainty = float(np.sqrt(np.dot(x, x)))
    exploration = cfg.alpha_ucb * uncertainty
    score = exploitation + exploration

    assert decision.ucb_exploitation == pytest.approx(exploitation)
    assert decision.ucb_exploration == pytest.approx(exploration)
    assert decision.ucb_score == pytest.approx(score)
    assert decision.ucb_alpha == pytest.approx(cfg.alpha_ucb)


def test_linucb_update_only_selected_arm_once() -> None:
    arms = [Arm(0.1, 6), Arm(0.2, 8)]
    cfg = LinUCBConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        arms=arms,
        warmup_per_arm=0,
        aoi_max_ms=1e9,
        mae_max=1e9,
        diagnostics_enabled=True,
    )
    pol = LinUCBPolicy(cfg)
    a_before = pol._A[1].copy()
    b_before = pol._b[1].copy()
    pol.decide(_make_state())
    r = pol.observe_outcome(aoi_ms=10.0, mae=0.1, rate_bps=100.0)
    assert math.isfinite(r)
    assert np.allclose(pol._A[1], a_before)
    assert np.allclose(pol._b[1], b_before)
    a_after = pol._A[0].copy()
    b_after = pol._b[0].copy()
    r2 = pol.observe_outcome(aoi_ms=10.0, mae=0.1, rate_bps=100.0)
    assert r2 == 0.0
    assert np.allclose(pol._A[0], a_after)
    assert np.allclose(pol._b[0], b_after)


def test_linucb_nan_context_skips_update() -> None:
    arms = [Arm(0.1, 6)]
    cfg = LinUCBConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        arms=arms,
        warmup_per_arm=0,
        aoi_max_ms=1e9,
        mae_max=1e9,
        diagnostics_enabled=True,
    )
    pol = LinUCBPolicy(cfg)
    pol.decide(_make_state(res=float("nan")))
    a_before = pol._A[0].copy()
    b_before = pol._b[0].copy()
    r = pol.observe_outcome(aoi_ms=10.0, mae=0.1, rate_bps=100.0)
    assert r == 0.0
    assert np.allclose(pol._A[0], a_before)
    assert np.allclose(pol._b[0], b_before)


def test_linucb_safe_arm_forcing_reasons() -> None:
    safe_arm = Arm(0.1, 10)
    arms = [Arm(0.2, 6), safe_arm]
    cfg = LinUCBConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        arms=arms,
        safe_arm=safe_arm,
        aoi_max_ms=1.0,
        mae_max=1.0,
        safety_force_emit_on_aoi=True,
        diagnostics_enabled=True,
    )
    pol = LinUCBPolicy(cfg)
    _, decision = pol.decide(_make_state(aoi_ms=10.0, res=0.0))
    assert decision.safe_arm_forced is True
    assert decision.forced_reason == "AOI_LIMIT"
    assert decision.tau == safe_arm.tau
    assert decision.kbits == safe_arm.kbits

    _, decision2 = pol.decide(_make_state(aoi_ms=10.0, res=2.0))
    assert decision2.safe_arm_forced is True
    assert decision2.forced_reason == "BOTH"


def test_linucb_singular_matrix_fallback() -> None:
    arms = [Arm(0.1, 6)]
    cfg = LinUCBConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        arms=arms,
        warmup_per_arm=0,
        diagnostics_enabled=True,
    )
    pol = LinUCBPolicy(cfg)
    pol._A[0] = np.zeros((pol.d, pol.d), dtype=np.float64)
    pol._b[0] = np.zeros((pol.d,), dtype=np.float64)
    _, decision = pol.decide(_make_state())
    assert decision.ucb_exploitation == 0.0
    assert decision.ucb_exploration == 0.0
    assert decision.ucb_score == 0.0
