from __future__ import annotations

from common.schema import LinkProfile, SensorType
from edge.predict.ewma import EWMAConfig, EWMAPredictor


class _TempSample:
    def __init__(self, ts_ns: int, seq: int, celsius: float, valid: bool = True) -> None:
        self.ts_ns = ts_ns
        self.seq = seq
        self.celsius = celsius
        self.valid = valid


def _make_predictor(**kwargs) -> EWMAPredictor:
    cfg_kwargs = {
        "device_id": "dev1",
        "sensor": SensorType.TEMP,
        "alpha": 0.5,
        "tau": 0.2,
        "kbits": 8,
        "profile": LinkProfile.SLOW_10KBPS,
        "heartbeat_s": None,
        "min_emit_interval_ms": 0,
        "bootstrap_emit": True,
        "diagnostics_enabled": True,
    }
    cfg_kwargs.update(kwargs)
    cfg = EWMAConfig(**cfg_kwargs)
    return EWMAPredictor(cfg)


def test_ewma_bootstrap_emit() -> None:
    pred = _make_predictor(tau=10.0, bootstrap_emit=True, heartbeat_s=None)
    evt = pred.predict_and_maybe_emit(_TempSample(0, 1, 25.0))
    assert evt is not None


def test_ewma_threshold_trigger() -> None:
    pred = _make_predictor(tau=0.5, bootstrap_emit=False, heartbeat_s=None)
    evt0 = pred.predict_and_maybe_emit(_TempSample(0, 1, 0.0))
    assert evt0 is None
    evt1 = pred.predict_and_maybe_emit(_TempSample(1_000_000_000, 2, 2.0))
    assert evt1 is not None
    assert evt1.event_reason == "THRESHOLD"


def test_ewma_heartbeat_trigger() -> None:
    pred = _make_predictor(tau=10.0, bootstrap_emit=False, heartbeat_s=1.0)
    evt0 = pred.predict_and_maybe_emit(_TempSample(0, 1, 10.0))
    assert evt0 is not None
    assert evt0.event_reason == "HEARTBEAT"
    evt1 = pred.predict_and_maybe_emit(_TempSample(500_000_000, 2, 10.0))
    assert evt1 is None
    evt2 = pred.predict_and_maybe_emit(_TempSample(1_500_000_000, 3, 10.0))
    assert evt2 is not None
    assert evt2.event_reason == "HEARTBEAT"


def test_ewma_rate_limit_skips_and_consume() -> None:
    pred = _make_predictor(
        tau=0.0,
        bootstrap_emit=True,
        heartbeat_s=None,
        min_emit_interval_ms=1000,
    )
    evt0 = pred.predict_and_maybe_emit(_TempSample(0, 1, 0.0))
    assert evt0 is not None
    evt1 = pred.predict_and_maybe_emit(_TempSample(100_000_000, 2, 1.0))
    assert evt1 is None
    skips = pred.consume_rate_limit_skips()
    assert skips == 1
    assert pred.consume_rate_limit_skips() == 0
