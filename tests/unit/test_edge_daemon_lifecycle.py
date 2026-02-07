from __future__ import annotations

from common.schema import LinkProfile, PolicyMode
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
        profile=LinkProfile.SLOW_10KBPS,
        outbox_path=str(tmp_path / "outbox.sqlite"),
        mode=PolicyMode.FIXED_TAU,
        mic=kwargs.get("mic", daemon_mod.MicCfg(enable=False)),
        temp=kwargs.get("temp", daemon_mod.TempCfg(enable=False)),
        rtc=kwargs.get("rtc", daemon_mod.RTCCfg(enable=False)),
        ui=kwargs.get("ui", daemon_mod.UICfg(enable=False)),
        buttons=kwargs.get("buttons", daemon_mod.ButtonsCfg(enable=False)),
        link=kwargs.get("link", daemon_mod.LinkCfg(apply_on_button=False, apply_on_start=False)),
    )


def test_rtc_init_failure_is_contained(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path, rtc=daemon_mod.RTCCfg(enable=True))

    def _raise_ds3231(*_args, **_kwargs):
        raise RuntimeError("rtc failed")

    monkeypatch.setattr(daemon_mod, "DS3231", _raise_ds3231)

    daemon._maybe_start_rtc()
    assert daemon._rtc_guardian is None
    assert daemon._rtc_device is None


def test_buttons_build_failure_is_contained(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path, buttons=daemon_mod.ButtonsCfg(enable=True))
    monkeypatch.setattr(
        daemon_mod,
        "build_buttons",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("buttons build failed")),
    )

    daemon._maybe_start_buttons()
    assert daemon._buttons is None


def test_buttons_start_failure_is_contained(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path, buttons=daemon_mod.ButtonsCfg(enable=True))

    class _BadButtons:
        def start(self) -> None:
            raise RuntimeError("start failed")

        def stop(self) -> None:
            return None

    monkeypatch.setattr(daemon_mod, "build_buttons", lambda *_args, **_kwargs: _BadButtons())

    daemon._maybe_start_buttons()
    assert daemon._buttons is not None


def test_link_profile_apply_failure_is_contained(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(
        monkeypatch,
        tmp_path,
        link=daemon_mod.LinkCfg(apply_on_button=True, apply_on_start=False),
    )
    monkeypatch.setattr(
        daemon_mod.tc_profiles,
        "apply_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("tc failed")),
    )

    daemon._apply_link_profile()


def test_start_stop_lifecycle_without_sensors(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(
        monkeypatch,
        tmp_path,
        mic=daemon_mod.MicCfg(enable=False),
        temp=daemon_mod.TempCfg(enable=False),
        ui=daemon_mod.UICfg(enable=False),
        buttons=daemon_mod.ButtonsCfg(enable=False),
        rtc=daemon_mod.RTCCfg(enable=False),
    )

    monkeypatch.setattr(daemon, "_install_signals", lambda: None)
    monkeypatch.setattr(daemon, "_maybe_start_rtc", lambda: None)
    monkeypatch.setattr(daemon, "_maybe_start_ui", lambda: None)
    monkeypatch.setattr(daemon, "_maybe_start_buttons", lambda: None)

    original_sleep = daemon_mod.time.sleep

    def _sleep_and_stop(_seconds):
        daemon.stop()

    monkeypatch.setattr(daemon_mod.time, "sleep", _sleep_and_stop)
    try:
        daemon.start()
    finally:
        monkeypatch.setattr(daemon_mod.time, "sleep", original_sleep)

    assert daemon.publisher.started is True
    assert daemon.publisher.stopped is True
    assert daemon.outbox.closed is True
