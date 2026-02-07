from __future__ import annotations

import argparse
import os

import pytest

from common.schema import LinkProfile, PolicyMode
from edge import edge_daemon as daemon_mod


class _DummyStats:
    ack_latency_ewma_ms = 0.0
    loss_ewma = 0.0


class _DummyOutbox:
    def __init__(self, _path: str):
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def pending(self) -> int:
        return 0

    def delivery_stats(self):
        return _DummyStats()

    def enqueue(self, *_args, **_kwargs):
        return None


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


def _make_daemon(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon_mod, "Outbox", _DummyOutbox)
    monkeypatch.setattr(daemon_mod, "MQTTPublisher", _DummyPublisher)
    return daemon_mod.EdgeDaemon(
        device_id="dev1",
        profile=LinkProfile.SLOW_10KBPS,
        outbox_path=str(tmp_path / "outbox.sqlite"),
        mode=PolicyMode.FIXED_TAU,
        mic=daemon_mod.MicCfg(enable=False),
        temp=daemon_mod.TempCfg(enable=False),
        rtc=daemon_mod.RTCCfg(enable=False),
        ui=daemon_mod.UICfg(enable=False),
        buttons=daemon_mod.ButtonsCfg(enable=False),
        link=daemon_mod.LinkCfg(apply_on_button=False, apply_on_start=False),
    )


def _mk_args(**overrides) -> argparse.Namespace:
    base = dict(
        device_id="dev1",
        mode="fixed_tau",
        profile="slow_10kbps",
        arms="configs/policy.yaml",
        run_dir=None,
        outbox=None,
        seed=None,
        mic_enable=True,
        mic_backend="auto",
        mic_arecord_device=None,
        mic_sd_device=None,
        mic_sr=16000,
        mic_frame_ms=100,
        mic_alpha=0.2,
        mic_tau=3.0,
        mic_kbits=6,
        mic_heartbeat=0.0,
        mic_min_emit_ms=0,
        temp_enable=False,
        temp_backend="mock",
        temp_hz=1.0,
        temp_alpha=0.5,
        temp_tau=0.2,
        temp_kbits=8,
        temp_heartbeat=0.0,
        temp_min_emit_ms=0,
        temp_w1_path=None,
        temp_sysfs_path=None,
        ui_enable=False,
        ui_kind="console",
        ui_bus=1,
        ui_address=None,
        ui_refresh=1.0,
        ui_rate_window=10.0,
        buttons_enable=False,
        btn_mode_pin=17,
        btn_profile_pin=27,
        btn_marker_pin=22,
        btn_debounce_ms=200,
        tc_iface="eth0",
        tc_both=False,
        tc_apply_on_button=False,
        tc_apply_on_start=False,
        tc_profiles_config=None,
        decision_publish="never",
        broker="localhost",
        port=1883,
        client_id="edge-pub",
        keepalive=30,
        username=None,
        password=None,
        tls=False,
        cafile=None,
        certfile=None,
        keyfile=None,
        base_topic="edge",
        rtc_enable=False,
        rtc_bus=1,
        rtc_address=0x68,
        rtc_drift_guard=2.0,
        rtc_resync=900.0,
        rtc_push_system=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_install_signals_registers_handlers_and_handler_stops(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path)
    handlers = {}

    def _register(sig, handler):
        handlers[sig] = handler

    stop_calls = {"count": 0}
    monkeypatch.setattr(daemon_mod.signal, "signal", _register)
    monkeypatch.setattr(
        daemon,
        "stop",
        lambda: stop_calls.__setitem__("count", stop_calls["count"] + 1),
    )
    monkeypatch.setattr(
        daemon_mod.sys,
        "exit",
        lambda code: (_ for _ in ()).throw(SystemExit(code)),
    )

    daemon._install_signals()

    assert daemon_mod.signal.SIGINT in handlers
    assert daemon_mod.signal.SIGTERM in handlers
    with pytest.raises(SystemExit) as excinfo:
        handlers[daemon_mod.signal.SIGTERM](15, None)
    assert excinfo.value.code == 0
    assert stop_calls["count"] == 1


def test_main_reads_seed_from_env_and_default_run_dir(monkeypatch) -> None:
    args = _mk_args(seed=None, run_dir=None, outbox=None, mode="fixed_tau", mic_enable=True)
    monkeypatch.setattr(daemon_mod, "parse_args", lambda _argv=None: args)
    monkeypatch.setattr(daemon_mod, "setup_logging_from_args", lambda _args: None)
    monkeypatch.setenv("SEMUP_SEED", "123")

    seeded = []
    run_dirs = []
    created = {}

    class _FakeDaemon:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.started = False
            self.stopped = False
            created["inst"] = self

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr(daemon_mod, "_seed_everything", lambda seed: seeded.append(seed))
    monkeypatch.setattr(
        daemon_mod,
        "_default_run_dir",
        lambda device_id: f"artifacts/{device_id}-run",
    )
    monkeypatch.setattr(daemon_mod, "_mk_run_dirs", lambda run_dir: run_dirs.append(run_dir))
    monkeypatch.setattr(daemon_mod, "EdgeDaemon", _FakeDaemon)

    daemon_mod.main([])

    assert seeded == [123]
    assert run_dirs == ["artifacts/dev1-run"]
    assert created["inst"].started is True
    assert created["inst"].stopped is True
    assert created["inst"].kwargs["seed"] == 123
    assert created["inst"].kwargs["arms_cfg"] is None
    assert created["inst"].kwargs["outbox_path"] == os.path.join(
        "artifacts/dev1-run",
        "outbox.sqlite",
    )
    assert created["inst"].kwargs["mic"].heartbeat_s is None
    assert created["inst"].kwargs["temp"].heartbeat_s is None


def test_main_adaptive_loads_policy_and_prefers_cli_seed(monkeypatch) -> None:
    args = _mk_args(
        mode="adaptive",
        seed=7,
        run_dir="artifacts/custom-run",
        outbox="artifacts/custom-run/custom.sqlite",
        mic_enable=True,
        temp_enable=True,
        temp_heartbeat=-1.0,
    )
    monkeypatch.setattr(daemon_mod, "parse_args", lambda _argv=None: args)
    monkeypatch.setattr(daemon_mod, "setup_logging_from_args", lambda _args: None)
    monkeypatch.setenv("SEMUP_SEED", "999")

    seeded = []
    policy_load_paths = []
    run_dirs = []
    created = {}
    policy_cfg = {"arms": [{"tau": 0.2, "kbits": 8}]}

    class _FakeDaemon:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.started = False
            self.stopped = False
            created["inst"] = self

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

    monkeypatch.setattr(daemon_mod, "_seed_everything", lambda seed: seeded.append(seed))
    monkeypatch.setattr(daemon_mod, "_mk_run_dirs", lambda run_dir: run_dirs.append(run_dir))
    monkeypatch.setattr(
        daemon_mod,
        "_load_policy_yaml",
        lambda path: (policy_load_paths.append(path), policy_cfg)[1],
    )
    monkeypatch.setattr(daemon_mod, "EdgeDaemon", _FakeDaemon)

    daemon_mod.main([])

    assert seeded == [7]
    assert run_dirs == ["artifacts/custom-run"]
    assert policy_load_paths == ["configs/policy.yaml"]
    assert created["inst"].started is True
    assert created["inst"].stopped is True
    assert created["inst"].kwargs["mode"] == PolicyMode.ADAPTIVE
    assert created["inst"].kwargs["arms_cfg"] == policy_cfg
    assert created["inst"].kwargs["seed"] == 7
    assert created["inst"].kwargs["outbox_path"] == "artifacts/custom-run/custom.sqlite"
    assert created["inst"].kwargs["temp"].heartbeat_s is None


def test_main_rejects_when_all_sensors_disabled(monkeypatch) -> None:
    args = _mk_args(mic_enable=False, temp_enable=False)
    monkeypatch.setattr(daemon_mod, "parse_args", lambda _argv=None: args)
    monkeypatch.setattr(daemon_mod, "setup_logging_from_args", lambda _args: None)
    monkeypatch.setattr(daemon_mod, "_seed_everything", lambda _seed: None)
    monkeypatch.setattr(
        daemon_mod,
        "EdgeDaemon",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("daemon must not be created")),
    )

    with pytest.raises(SystemExit) as excinfo:
        daemon_mod.main([])
    assert excinfo.value.code == 2
