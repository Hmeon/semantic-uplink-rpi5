from __future__ import annotations

from common.schema import LinkProfile, PolicyMode, SensorType
from edge import edge_daemon as daemon_mod


class _DummyStats:
    ack_latency_ewma_ms = 0.0
    loss_ewma = 0.0


class _DummyOutbox:
    def __init__(self, _path: str):
        self.closed = False
        self.enqueued = []

    def close(self) -> None:
        self.closed = True

    def pending(self) -> int:
        return 0

    def delivery_stats(self):
        return _DummyStats()

    def enqueue(self, topic, payload, qos, retain, created_ns):
        self.enqueued.append((topic, payload, qos, retain, created_ns))


class _DummyPublisher:
    def __init__(self, outbox, **_kwargs):
        self.outbox = outbox
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def is_connected(self) -> bool:
        return True


def _make_daemon(monkeypatch, tmp_path, **kwargs):
    monkeypatch.setattr(daemon_mod, "Outbox", _DummyOutbox)
    monkeypatch.setattr(daemon_mod, "MQTTPublisher", _DummyPublisher)
    return daemon_mod.EdgeDaemon(
        device_id="dev1",
        profile=kwargs.get("profile", LinkProfile.SLOW_10KBPS),
        outbox_path=str(tmp_path / "outbox.sqlite"),
        mode=kwargs.get("mode", PolicyMode.FIXED_TAU),
        arms_cfg=kwargs.get("arms_cfg"),
        mic=kwargs.get("mic", daemon_mod.MicCfg(enable=False)),
        temp=kwargs.get("temp", daemon_mod.TempCfg(enable=False)),
        rtc=daemon_mod.RTCCfg(enable=False),
        ui=daemon_mod.UICfg(enable=False),
        buttons=daemon_mod.ButtonsCfg(enable=False),
        link=daemon_mod.LinkCfg(apply_on_button=False, apply_on_start=False),
    )


def test_cycle_mode_rejects_adaptive_without_arms_config(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path, mode=PolicyMode.FIXED_TAU, arms_cfg={})
    seen_policy_updates = []
    daemon._status.update_policy = lambda **kwargs: seen_policy_updates.append(kwargs)  # type: ignore[method-assign]

    refreshed = {"count": 0}
    daemon._refresh_policies = lambda: refreshed.__setitem__("count", refreshed["count"] + 1)  # type: ignore[method-assign]

    daemon._cycle_mode()

    assert daemon.mode == PolicyMode.FIXED_TAU
    assert seen_policy_updates == []
    assert refreshed["count"] == 0


def test_cycle_mode_advances_with_arms_and_refreshes(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(
        monkeypatch,
        tmp_path,
        mode=PolicyMode.FIXED_TAU,
        arms_cfg={"arms": [{"tau": 0.2, "kbits": 8}]},
    )
    seen_modes = []
    daemon._status.update_policy = lambda **kwargs: seen_modes.append(kwargs.get("mode"))  # type: ignore[method-assign]

    refreshed = {"count": 0}
    daemon._refresh_policies = lambda: refreshed.__setitem__("count", refreshed["count"] + 1)  # type: ignore[method-assign]

    daemon._cycle_mode()
    daemon._cycle_mode()

    assert seen_modes == [PolicyMode.ADAPTIVE, PolicyMode.PERIODIC]
    assert daemon.mode == PolicyMode.PERIODIC
    assert refreshed["count"] == 2


def test_cycle_profile_rotates_and_triggers_apply_refresh(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path, profile=LinkProfile.SLOW_10KBPS)
    seen_profiles = []
    daemon._status.update_policy = lambda **kwargs: seen_profiles.append(kwargs.get("profile"))  # type: ignore[method-assign]

    applied = {"count": 0}
    refreshed = {"count": 0}
    daemon._apply_link_profile = lambda: applied.__setitem__("count", applied["count"] + 1)  # type: ignore[method-assign]
    daemon._refresh_policies = lambda: refreshed.__setitem__("count", refreshed["count"] + 1)  # type: ignore[method-assign]

    daemon._cycle_profile()
    daemon._cycle_profile()
    daemon._cycle_profile()

    assert seen_profiles == [
        LinkProfile.DELAY_LOSS,
        LinkProfile.CELLULAR_VAR,
        LinkProfile.SLOW_10KBPS,
    ]
    assert daemon.profile == LinkProfile.SLOW_10KBPS
    assert applied["count"] == 3
    assert refreshed["count"] == 3


def test_refresh_policies_rebuilds_enabled_sensor_runtimes(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(
        monkeypatch,
        tmp_path,
        mic=daemon_mod.MicCfg(enable=True, frame_ms=80),
        temp=daemon_mod.TempCfg(enable=True, sample_hz=0.0),
    )
    calls = []

    def _build_policy_runtime(**kwargs):
        calls.append(kwargs)
        return f"runtime-{kwargs['sensor'].value}"

    monkeypatch.setattr(daemon, "_build_policy_runtime", _build_policy_runtime)
    daemon._refresh_policies()

    assert len(calls) == 2
    assert calls[0]["sensor"] == SensorType.MIC_RMS
    assert calls[0]["nominal_period_s"] == 0.08
    assert calls[1]["sensor"] == SensorType.TEMP
    assert calls[1]["nominal_period_s"] == 1.0
    assert daemon._mic_policy == "runtime-mic_rms"
    assert daemon._temp_policy == "runtime-temp"
