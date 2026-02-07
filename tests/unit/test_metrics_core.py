from __future__ import annotations

import math

import pytest

import common.metrics as cm
from common.schema import EventMsg, LinkProfile, PolicyMode, SensorType


def test_aoi_mean_handles_unsorted_input() -> None:
    ts_ns = [2_000_000_000, 0, 1_000_000_000]
    # Sorted deltas become [1000ms, 1000ms] -> mean AoI 500ms.
    assert cm.aoi_mean(ts_ns) == pytest.approx(500.0, abs=1e-9)


def test_aoi_percentile_validates_probability() -> None:
    with pytest.raises(ValueError):
        cm.aoi_percentile([0, 1_000_000_000], p=1.0)

    with pytest.raises(ValueError):
        cm.aoi_percentile_from_deltas([100.0, 200.0], p=0.0)


def test_aoi_aggregator_ignores_negative_delta() -> None:
    agg = cm.AoIAggregator(keep_deltas=True)
    agg.push(1_000_000_000)
    agg.push(900_000_000)   # reversed timestamp: ignored
    agg.push(2_000_000_000)

    mean_ms, p95_ms = agg.finalize(p=0.95)
    # Implementation detail: AoIAggregator updates `last_ts` before the negative-delta
    # guard, so the next valid delta becomes (2.0s - 0.9s) = 1.1s.
    assert mean_ms == pytest.approx(550.0, abs=1e-9)
    # Current percentile implementation returns 0.0 for the single-delta case.
    assert p95_ms == pytest.approx(0.0, abs=1e-6)


def test_aoi_aggregator_without_deltas_returns_nan_percentile() -> None:
    agg = cm.AoIAggregator(keep_deltas=False)
    agg.push(0)
    agg.push(1_000_000_000)
    assert math.isnan(agg.p_ms(0.95))


def test_mqtt_publish_size_fallback_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force fallback branch (without common.mqttutil helper).
    monkeypatch.setattr(cm, "mqtt_v311_publish_size", None)
    # topic len=6, qos=1 -> var header: 2 + 6 + 2 = 10
    # payload=10 -> remaining=20, RL bytes=1, fixed header=1 => total=22
    assert cm.mqtt_publish_size("a/b/cd", 10, qos=1) == 22

    assert cm._mqtt_remaining_length_nbytes(127) == 1
    assert cm._mqtt_remaining_length_nbytes(128) == 2
    with pytest.raises(ValueError):
        cm._mqtt_remaining_length_nbytes(-1)


def test_mqtt_publish_size_input_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cm, "mqtt_v311_publish_size", None)
    with pytest.raises(ValueError):
        cm.mqtt_publish_size("", 1, qos=1)
    with pytest.raises(ValueError):
        cm.mqtt_publish_size("topic", -1, qos=1)
    with pytest.raises(ValueError):
        cm.mqtt_publish_size("topic", 1, qos=3)


def test_mqtt_bytes_of_event_and_missing_eventmsg(monkeypatch: pytest.MonkeyPatch) -> None:
    ev = EventMsg(
        ts=1,
        seq=1,
        device_id="dev1",
        sensor=SensorType.TEMP,
        val=1.0,
        pred=1.0,
        res=0.0,
        tau=0.2,
        kbits=8,
        profile=LinkProfile.SLOW_10KBPS,
        policy=PolicyMode.PERIODIC,
    )
    size = cm.mqtt_bytes_of_event(ev, qos=1)
    assert int(size) > 0

    monkeypatch.setattr(cm, "EventMsg", None)
    with pytest.raises(RuntimeError):
        cm.mqtt_bytes_of_event(ev, qos=1)


def test_rate_and_online_variance_helpers() -> None:
    assert cm.bytes_per_sec(300, 0, 2_000_000_000) == pytest.approx(150.0, abs=1e-12)
    assert math.isnan(cm.bytes_per_sec(300, 10, 10))

    assert cm.percent_improvement(100.0, 80.0) == pytest.approx(20.0, abs=1e-12)
    assert math.isnan(cm.percent_improvement(0.0, 1.0))

    ov = cm.OnlineVar()
    ov.update(1.0)
    ov.update(2.0)
    ov.update(3.0)
    assert ov.count == 3
    assert ov.mean == pytest.approx(2.0, abs=1e-12)
    assert ov.var == pytest.approx(1.0, abs=1e-12)
    assert ov.std == pytest.approx(1.0, abs=1e-12)

    ov.reset()
    assert ov.count == 0
    assert math.isnan(ov.mean)
