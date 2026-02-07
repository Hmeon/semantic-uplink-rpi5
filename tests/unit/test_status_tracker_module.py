from __future__ import annotations

from common.schema import LinkProfile, PolicyMode, SensorType
from edge.ui.status import StatusTracker


def test_status_tracker_snapshot_reflects_latest_state() -> None:
    tracker = StatusTracker(
        profile=LinkProfile.SLOW_10KBPS,
        mode=PolicyMode.FIXED_TAU,
        rate_window_s=10.0,
    )

    tracker.update_temp(24.5, True)
    tracker.update_mic(-12.3, 0.07)
    tracker.update_policy(profile=LinkProfile.DELAY_LOSS, mode=PolicyMode.ADAPTIVE)
    tracker.record_metrics(
        sensor=SensorType.TEMP,
        aoi_ms=123.0,
        mae=0.42,
        rate_bps=999.0,
    )

    snap = tracker.snapshot(mqtt_connected=True, outbox_pending=5)
    assert snap.profile == LinkProfile.DELAY_LOSS
    assert snap.mode == PolicyMode.ADAPTIVE
    assert snap.temp_c == 24.5
    assert snap.temp_valid is True
    assert snap.mic_dbfs == -12.3
    assert snap.mic_clip == 0.07
    assert snap.mqtt_connected is True
    assert snap.outbox_pending == 5
    assert snap.aoi_ms == 123.0
    assert snap.mae == 0.42


def test_status_tracker_rate_window_contract(monkeypatch) -> None:
    tracker = StatusTracker(
        profile=LinkProfile.SLOW_10KBPS,
        mode=PolicyMode.PERIODIC,
        rate_window_s=1.0,
    )

    tracker.record_payload(100, ts_ns=1_000_000_000)  # 800 bits
    tracker.record_payload(50, ts_ns=1_500_000_000)   # 400 bits

    monkeypatch.setattr("edge.ui.status.time.time_ns", lambda: 2_000_000_000)
    snap = tracker.snapshot(mqtt_connected=False, outbox_pending=0)
    assert abs(snap.tx_rate_bps - 1200.0) < 1e-6

    # Advance time enough to evict both payload samples from the 1s window.
    monkeypatch.setattr("edge.ui.status.time.time_ns", lambda: 2_600_000_000)
    snap2 = tracker.snapshot(mqtt_connected=False, outbox_pending=0)
    assert snap2.tx_rate_bps == 0.0


def test_status_tracker_record_payload_clamps_negative_length(monkeypatch) -> None:
    tracker = StatusTracker(
        profile=LinkProfile.SLOW_10KBPS,
        mode=PolicyMode.PERIODIC,
        rate_window_s=2.0,
    )

    tracker.record_payload(-123, ts_ns=1_000_000_000)
    monkeypatch.setattr("edge.ui.status.time.time_ns", lambda: 1_500_000_000)
    snap = tracker.snapshot(mqtt_connected=False, outbox_pending=0)
    assert snap.tx_rate_bps == 0.0
