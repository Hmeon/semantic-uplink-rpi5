from __future__ import annotations

import pytest

from common.schema import LinkProfile, PolicyMode, SensorType
from edge.policy.linucb import Arm, LinUCBConfig
from edge.policy.runtime import SensorPolicyRuntime
from edge.predict.ewma import EWMAConfig


class _TempSample:
    def __init__(self, ts_ns: int, seq: int, celsius: float, valid: bool) -> None:
        self.ts_ns = ts_ns
        self.seq = seq
        self.celsius = celsius
        self.valid = valid


def test_policy_runtime_returns_stepresult_on_invalid_sample() -> None:
    rt = SensorPolicyRuntime(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        mode=PolicyMode.FIXED_TAU,
        ewma_cfg=EWMAConfig(
            device_id="dev1",
            sensor=SensorType.TEMP,
            alpha=0.5,
            tau=0.2,
            kbits=8,
            profile=LinkProfile.SLOW_10KBPS,
            heartbeat_s=None,
            bootstrap_emit=True,
        ),
        nominal_period_s=1.0,
    )

    first = _TempSample(ts_ns=1_000_000_000, seq=1, celsius=25.0, valid=True)
    res0 = rt.step(first, outbox_pending=0)
    assert res0.event is not None

    invalid = _TempSample(ts_ns=1_500_000_000, seq=2, celsius=25.0, valid=False)
    res1 = rt.step(invalid, outbox_pending=0)
    assert res1.event is None
    assert res1.decision is None
    assert res1.reward is None
    assert res1.aoi_ms == pytest.approx(500.0, abs=1e-6)
    assert res1.mae_est >= 0.0
    assert res1.rate_bps == 0.0


def test_policy_runtime_safety_force_emit_on_aoi() -> None:
    ewma_cfg = EWMAConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        alpha=0.5,
        tau=100.0,
        kbits=8,
        profile=LinkProfile.SLOW_10KBPS,
        heartbeat_s=None,
        bootstrap_emit=True,
        diagnostics_enabled=True,
    )
    linucb_cfg = LinUCBConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        arms=[Arm(tau=100.0, kbits=8)],
        aoi_max_ms=10.0,
        mae_max=999.0,
        safety_force_emit_on_aoi=True,
        diagnostics_enabled=True,
    )
    rt = SensorPolicyRuntime(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        mode=PolicyMode.ADAPTIVE,
        ewma_cfg=ewma_cfg,
        linucb_cfg=linucb_cfg,
        nominal_period_s=1.0,
    )

    first = _TempSample(ts_ns=0, seq=1, celsius=25.0, valid=True)
    res0 = rt.step(first, outbox_pending=0)
    assert res0.event is not None

    second = _TempSample(ts_ns=1_000_000_000, seq=2, celsius=25.0, valid=True)
    res1 = rt.step(second, outbox_pending=0)
    assert res1.event is not None
    assert res1.event.event_reason == "SAFETY_AOI"


def test_policy_runtime_no_force_emit_by_default() -> None:
    ewma_cfg = EWMAConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        alpha=0.5,
        tau=100.0,
        kbits=8,
        profile=LinkProfile.SLOW_10KBPS,
        heartbeat_s=None,
        bootstrap_emit=True,
        diagnostics_enabled=True,
    )
    linucb_cfg = LinUCBConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        arms=[Arm(tau=100.0, kbits=8)],
        aoi_max_ms=10.0,
        mae_max=999.0,
        safety_force_emit_on_aoi=False,
        diagnostics_enabled=True,
    )
    rt = SensorPolicyRuntime(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        mode=PolicyMode.ADAPTIVE,
        ewma_cfg=ewma_cfg,
        linucb_cfg=linucb_cfg,
        nominal_period_s=1.0,
    )

    first = _TempSample(ts_ns=0, seq=1, celsius=25.0, valid=True)
    res0 = rt.step(first, outbox_pending=0)
    assert res0.event is not None

    second = _TempSample(ts_ns=1_000_000_000, seq=2, celsius=25.0, valid=True)
    res1 = rt.step(second, outbox_pending=0)
    assert res1.event is None


def test_policy_runtime_coverage_force_emit_on_unhit_segment() -> None:
    ewma_cfg = EWMAConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        alpha=0.5,
        tau=0.2,  # tau_ref for segment tracking
        kbits=8,
        profile=LinkProfile.SLOW_10KBPS,
        heartbeat_s=None,
        bootstrap_emit=True,
        diagnostics_enabled=True,
    )
    linucb_cfg = LinUCBConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        arms=[Arm(tau=100.0, kbits=8)],
        aoi_max_ms=1e9,
        mae_max=1e9,
        residual_guard_enabled=False,
        safety_force_emit_on_aoi=False,
        coverage_force_emit_on_unhit_segment=True,
        diagnostics_enabled=True,
    )
    rt = SensorPolicyRuntime(
        device_id="dev1",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        mode=PolicyMode.ADAPTIVE,
        ewma_cfg=ewma_cfg,
        linucb_cfg=linucb_cfg,
        nominal_period_s=1.0,
    )

    first = _TempSample(ts_ns=0, seq=1, celsius=0.0, valid=True)
    res0 = rt.step(first, outbox_pending=0)
    assert res0.event is not None

    # Start an anomaly segment (len=1): should NOT force emit yet.
    second = _TempSample(ts_ns=1_000_000_000, seq=2, celsius=10.0, valid=True)
    res1 = rt.step(second, outbox_pending=0)
    assert res1.event is None

    # Segment len>=2 and still un-hit: force a single emit (keep LinUCB-chosen tau/kbits).
    third = _TempSample(ts_ns=2_000_000_000, seq=3, celsius=20.0, valid=True)
    res2 = rt.step(third, outbox_pending=0)
    assert res2.event is not None
    assert res2.event.tau == pytest.approx(100.0)
    assert res2.event.event_reason == "COVERAGE_SEG"
