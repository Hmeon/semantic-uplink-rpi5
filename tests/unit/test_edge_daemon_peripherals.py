from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

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


def test_maybe_start_rtc_unavailable_guard_cleans_up_device(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path, rtc=daemon_mod.RTCCfg(enable=True))
    created = {}

    class _FakeDS3231:
        def __init__(self, **_kwargs):
            self.closed = False
            created["device"] = self

        def close(self) -> None:
            self.closed = True

    class _FakeGuardian:
        def __init__(self, device, **_kwargs):
            self.device = device
            self.started = False

        def guard_once(self):
            return SimpleNamespace(rtc_time=None, drift_seconds=None, last_error="no rtc")

        def start(self):
            self.started = True

    monkeypatch.setattr(daemon_mod, "DS3231", _FakeDS3231)
    monkeypatch.setattr(daemon_mod, "RTCGuardian", _FakeGuardian)

    daemon._maybe_start_rtc()

    assert daemon._rtc_guardian is None
    assert daemon._rtc_device is None
    assert created["device"].closed is True


def test_maybe_start_rtc_success_starts_and_stop_rtc_cleans_up(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(
        monkeypatch,
        tmp_path,
        rtc=daemon_mod.RTCCfg(enable=True, resync_interval_s=15.0),
    )
    created = {}

    class _FakeDS3231:
        def __init__(self, **_kwargs):
            self.closed = False
            created["device"] = self

        def close(self) -> None:
            self.closed = True

    class _FakeGuardian:
        def __init__(self, device, **_kwargs):
            self.device = device
            self.started = False
            self.stopped = False
            created["guardian"] = self

        def guard_once(self):
            return SimpleNamespace(
                rtc_time=datetime(2026, 2, 7, tzinfo=timezone.utc),
                drift_seconds=0.1,
                last_error=None,
            )

        def start(self):
            self.started = True

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(daemon_mod, "DS3231", _FakeDS3231)
    monkeypatch.setattr(daemon_mod, "RTCGuardian", _FakeGuardian)

    daemon._maybe_start_rtc()
    assert daemon._rtc_guardian is created["guardian"]
    assert created["guardian"].started is True

    daemon._stop_rtc()
    assert created["guardian"].stopped is True
    assert created["device"].closed is True
    assert daemon._rtc_guardian is None
    assert daemon._rtc_device is None


def test_maybe_start_ui_build_none_skips_thread_start(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path, ui=daemon_mod.UICfg(enable=True, kind="console"))
    monkeypatch.setattr(daemon_mod, "build_display", lambda _cfg: None)
    monkeypatch.setattr(
        daemon_mod.threading,
        "Thread",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("thread should not start")),
    )

    daemon._maybe_start_ui()

    assert daemon._display is None
    assert daemon._ui_thread is None


def test_maybe_start_ui_success_builds_display_and_starts_thread(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path, ui=daemon_mod.UICfg(enable=True, kind="console"))
    display = object()
    monkeypatch.setattr(daemon_mod, "build_display", lambda _cfg: display)

    created = {}

    class _FakeThread:
        def __init__(self, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            self.started = False
            created["thread"] = self

        def start(self):
            self.started = True

    monkeypatch.setattr(daemon_mod.threading, "Thread", _FakeThread)

    daemon._maybe_start_ui()

    assert daemon._display is display
    assert daemon._ui_thread is created["thread"]
    assert created["thread"].target == daemon._ui_loop
    assert created["thread"].name == "edge-ui"
    assert created["thread"].daemon is True
    assert created["thread"].started is True


def test_apply_link_profile_loads_override_once_and_reuses_cache(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(
        monkeypatch,
        tmp_path,
        link=daemon_mod.LinkCfg(
            apply_on_button=True,
            apply_on_start=False,
            profiles_config="configs/link_profiles.yaml",
            iface="eth0",
            both=True,
        ),
    )
    load_calls = []
    apply_calls = []

    def _load_profiles(path):
        load_calls.append(path)
        return {"custom": {"rate": "10kbit"}}

    def _apply_profile(iface, profile, both=False, profiles=None):
        apply_calls.append((iface, profile, bool(both), profiles))

    monkeypatch.setattr(daemon_mod.tc_profiles, "load_profiles_config", _load_profiles)
    monkeypatch.setattr(daemon_mod.tc_profiles, "apply_profile", _apply_profile)

    daemon._apply_link_profile()
    daemon._apply_link_profile()

    assert load_calls == ["configs/link_profiles.yaml"]
    assert len(apply_calls) == 2
    assert all(call[0] == "eth0" for call in apply_calls)
    assert all(call[1] == daemon.profile.value for call in apply_calls)
    assert all(call[2] is True for call in apply_calls)
    assert all(call[3] == {"custom": {"rate": "10kbit"}} for call in apply_calls)


def test_apply_link_profile_load_failure_falls_back_to_default_profiles(
    monkeypatch, tmp_path
) -> None:
    daemon = _make_daemon(
        monkeypatch,
        tmp_path,
        link=daemon_mod.LinkCfg(
            apply_on_button=True,
            apply_on_start=False,
            profiles_config="broken.yaml",
            iface="wlan0",
            both=False,
        ),
    )
    apply_calls = []
    monkeypatch.setattr(
        daemon_mod.tc_profiles,
        "load_profiles_config",
        lambda _path: (_ for _ in ()).throw(RuntimeError("broken config")),
    )
    monkeypatch.setattr(
        daemon_mod.tc_profiles,
        "apply_profile",
        lambda iface, profile, both=False, profiles=None: apply_calls.append(
            (iface, profile, bool(both), profiles)
        ),
    )

    daemon._apply_link_profile()

    assert apply_calls == [("wlan0", daemon.profile.value, False, None)]


def test_stop_joins_threads_and_runs_cleanup_paths(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(
        monkeypatch,
        tmp_path,
        mic=daemon_mod.MicCfg(enable=False),
        temp=daemon_mod.TempCfg(enable=False),
    )

    class _Closable:
        def __init__(self, *, fail=False):
            self.closed = False
            self.fail = fail

        def close(self):
            self.closed = True
            if self.fail:
                raise RuntimeError("close failed")

    class _Stoppable:
        def __init__(self, *, fail=False):
            self.stopped = False
            self.fail = fail

        def stop(self):
            self.stopped = True
            if self.fail:
                raise RuntimeError("stop failed")

    class _AliveThread:
        def __init__(self):
            self.joined = False

        def is_alive(self):
            return True

        def join(self, timeout=None):
            self.joined = True

    mic_obj = _Closable(fail=True)
    temp_obj = _Closable(fail=True)
    display = _Closable(fail=True)
    buttons = _Stoppable(fail=True)
    rtc_dev = _Closable()

    class _RtcGuardian:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

    rtc_guard = _RtcGuardian()
    ui_thread = _AliveThread()
    mic_thread = _AliveThread()
    temp_thread = _AliveThread()

    daemon._mic_obj = mic_obj
    daemon._temp_obj = temp_obj
    daemon._display = display
    daemon._buttons = buttons
    daemon._rtc_device = rtc_dev
    daemon._rtc_guardian = rtc_guard
    daemon._ui_thread = ui_thread
    daemon._mic_thread = mic_thread
    daemon._temp_thread = temp_thread

    daemon.stop()

    assert daemon._stop.is_set() is True
    assert ui_thread.joined is True
    assert mic_thread.joined is True
    assert temp_thread.joined is True
    assert buttons.stopped is True
    assert rtc_guard.stopped is True
    assert rtc_dev.closed is True
    assert daemon.publisher.stopped is True
    assert daemon.outbox.closed is True
