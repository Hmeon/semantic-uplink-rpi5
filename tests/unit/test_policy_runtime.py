from __future__ import annotations

import pytest

from common.schema import LinkProfile, PolicyMode, SensorType
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
