from __future__ import annotations

from common.schema import LinkProfile, SensorType
from edge.policy.linucb import Arm, LinUCBConfig, LinUCBPolicy, PolicyState


def test_linucb_policy_safe_guard_selects_safe_arm() -> None:
    arms = [Arm(tau=0.05, kbits=6), Arm(tau=0.05, kbits=10), Arm(tau=0.2, kbits=6)]
    cfg = LinUCBConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        seed=None,
        arms=arms,
        aoi_max_ms=1.0,
        mae_max=999.0,
    )
    pol = LinUCBPolicy(cfg)

    state = PolicyState(ts_ns=1, aoi_ms=10.0, res=0.0, res_var=0.0, loss=0.0, q_len=0)
    (tau, kbits), msg = pol.decide(state)
    assert (tau, kbits) == (0.05, 10)  # tau_min + kbits_max
    assert msg.tau == 0.05
    assert msg.kbits == 10
    assert msg.reward == 0.0


def test_linucb_policy_warmup_cycles_arms_in_order() -> None:
    arms = [Arm(tau=0.1, kbits=6), Arm(tau=0.2, kbits=6), Arm(tau=0.3, kbits=6)]
    cfg = LinUCBConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        seed=None,
        arms=arms,
        warmup_per_arm=1,
        aoi_max_ms=10_000.0,
        mae_max=10_000.0,
        alpha_ucb=0.1,
    )
    pol = LinUCBPolicy(cfg)

    state = PolicyState(ts_ns=1, aoi_ms=10.0, res=0.0, res_var=0.0, loss=0.0, q_len=0)
    chosen = []
    for _ in range(len(arms)):
        (tau, kbits), _msg = pol.decide(state)
        chosen.append((tau, kbits))
        pol.observe_outcome(aoi_ms=10.0, mae=0.0, rate_bps=0.0)

    assert chosen == [(0.1, 6), (0.2, 6), (0.3, 6)]


def test_linucb_policy_observe_outcome_without_decide_is_noop() -> None:
    cfg = LinUCBConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        seed=None,
        arms=[Arm(tau=0.1, kbits=6)],
    )
    pol = LinUCBPolicy(cfg)
    assert pol.observe_outcome(aoi_ms=1.0, mae=1.0, rate_bps=1.0) == 0.0

