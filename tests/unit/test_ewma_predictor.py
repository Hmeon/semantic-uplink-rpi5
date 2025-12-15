from __future__ import annotations

from dataclasses import dataclass

from common.schema import LinkProfile, PolicyMode, SensorType
from edge.predict.ewma import EWMAConfig, EWMAPredictor


@dataclass(slots=True)
class TempSample:
    ts_ns: int
    seq: int
    celsius: float
    valid: bool = True


def test_ewma_predictor_emits_on_residual_exceeding_tau() -> None:
    cfg = EWMAConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        alpha=0.5,
        tau=0.2,
        kbits=8,
        profile=LinkProfile.SLOW_10KBPS,
        heartbeat_s=None,
        bootstrap_emit=False,
    )
    pred = EWMAPredictor(cfg)

    # First sample bootstraps the predictor (residual=0).
    assert pred.predict_and_maybe_emit(TempSample(ts_ns=1, seq=1, celsius=0.0)) is None
    assert pred.predict_and_maybe_emit(TempSample(ts_ns=2, seq=2, celsius=0.1)) is None

    evt = pred.predict_and_maybe_emit(
        TempSample(ts_ns=3, seq=3, celsius=0.6), policy_mode=PolicyMode.FIXED_TAU
    )
    assert evt is not None
    assert evt.seq == 3
    assert evt.tau == 0.2
    assert evt.kbits == 8


def test_ewma_predictor_periodic_override_tau_emits_every_time() -> None:
    cfg = EWMAConfig(
        device_id="dev1",
        sensor=SensorType.TEMP,
        alpha=0.5,
        tau=0.2,
        kbits=8,
        profile=LinkProfile.SLOW_10KBPS,
        heartbeat_s=None,
        bootstrap_emit=False,
    )
    pred = EWMAPredictor(cfg)
    for i in range(3):
        evt = pred.predict_and_maybe_emit(
            TempSample(ts_ns=1 + i, seq=1 + i, celsius=0.0),
            override_tau=-1e-9,
            policy_mode=PolicyMode.PERIODIC,
        )
        assert evt is not None
        assert evt.policy == PolicyMode.PERIODIC

