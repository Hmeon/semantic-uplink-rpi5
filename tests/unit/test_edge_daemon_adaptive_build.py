from __future__ import annotations

import math

import pytest

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


def _make_daemon(monkeypatch, tmp_path, *, mode=PolicyMode.ADAPTIVE, arms_cfg=None):
    monkeypatch.setattr(daemon_mod, "Outbox", _DummyOutbox)
    monkeypatch.setattr(daemon_mod, "MQTTPublisher", _DummyPublisher)
    return daemon_mod.EdgeDaemon(
        device_id="dev1",
        profile=LinkProfile.SLOW_10KBPS,
        outbox_path=str(tmp_path / "outbox.sqlite"),
        mode=mode,
        arms_cfg=arms_cfg,
        mic=daemon_mod.MicCfg(enable=False),
        temp=daemon_mod.TempCfg(enable=False),
        rtc=daemon_mod.RTCCfg(enable=False),
        ui=daemon_mod.UICfg(enable=False),
        buttons=daemon_mod.ButtonsCfg(enable=False),
        link=daemon_mod.LinkCfg(apply_on_button=False, apply_on_start=False),
    )


def test_make_linucb_config_merges_sensor_overrides_and_scales(monkeypatch, tmp_path) -> None:
    arms_cfg = {
        "arms": [{"tau": 1.5, "kbits": 6}],
        "reward": {"alpha": 1.0, "beta": 2.0, "gamma": 3.0},
        "safety": {"aoi_max_ms": 5000.0, "mae_max": 2.5},
        "diagnostics": {"enabled": False, "events_enabled": False},
        "scales": {"q_len": 10.0},
        "linucb": {"alpha_ucb": 0.7, "warmup_per_arm": 2},
        "sensors": {
            "temp": {
                "arms": [{"tau": 0.4, "kbits": 9}],
                "reward": {"beta": 5.0},
                "safety": {"mae_max": 0.9},
                "diagnostics": {"enabled": True},
                "scales": {"q_len": 77.0},
                "linucb": {"alpha_ucb": 0.42},
            }
        },
    }
    daemon = _make_daemon(monkeypatch, tmp_path, arms_cfg=arms_cfg)

    captured = {}

    def _fake_load_linucb_config(cfg, **kwargs):
        captured["cfg"] = cfg
        captured["kwargs"] = kwargs
        return {"sentinel": True}

    monkeypatch.setattr(daemon_mod, "load_linucb_config", _fake_load_linucb_config)
    out = daemon._make_linucb_config(SensorType.TEMP)

    assert out == {"sentinel": True}
    assert captured["cfg"]["arms"] == [{"tau": 0.4, "kbits": 9}]
    assert captured["cfg"]["reward"] == {"alpha": 1.0, "beta": 5.0, "gamma": 3.0}
    assert captured["cfg"]["safety"] == {"aoi_max_ms": 5000.0, "mae_max": 0.9}
    assert captured["cfg"]["diagnostics"] == {"enabled": True, "events_enabled": False}
    assert captured["cfg"]["scales"] == {"q_len": 77.0}
    assert captured["cfg"]["linucb"] == {"alpha_ucb": 0.42, "warmup_per_arm": 2}
    assert captured["kwargs"]["device_id"] == "dev1"
    assert captured["kwargs"]["sensor"] == SensorType.TEMP
    assert captured["kwargs"]["profile"] == LinkProfile.SLOW_10KBPS
    assert captured["kwargs"]["seed"] is None
    assert math.isclose(captured["kwargs"]["mae_scale"], 0.4)
    assert math.isclose(captured["kwargs"]["res_scale"], 0.4)
    assert math.isclose(captured["kwargs"]["resvar_scale"], 0.16)


def test_make_linucb_config_rejects_empty_arms(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path, arms_cfg={"arms": []})
    with pytest.raises(ValueError, match="adaptive mode requires arms"):
        daemon._make_linucb_config(SensorType.MIC_RMS)


def test_make_linucb_config_nonpositive_tau_uses_safe_scale(monkeypatch, tmp_path) -> None:
    daemon = _make_daemon(monkeypatch, tmp_path, arms_cfg={"arms": [{"tau": 0.0, "kbits": 8}]})

    captured = {}

    def _fake_load_linucb_config(_cfg, **kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(daemon_mod, "load_linucb_config", _fake_load_linucb_config)
    daemon._make_linucb_config(SensorType.MIC_RMS)

    assert captured["mae_scale"] == 1.0
    assert captured["res_scale"] == 1.0
    assert captured["resvar_scale"] == 1.0


def test_build_policy_runtime_adaptive_uses_sensor_diag_event_override(
    monkeypatch, tmp_path
) -> None:
    arms_cfg = {
        "arms": [{"tau": 0.3, "kbits": 8}],
        "diagnostics": {"enabled": True, "events_enabled": True},
        "sensors": {"temp": {"diagnostics": {"events_enabled": False}}},
    }
    daemon = _make_daemon(monkeypatch, tmp_path, mode=PolicyMode.ADAPTIVE, arms_cfg=arms_cfg)

    captured = {}

    class _FakeRuntime:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(daemon_mod, "SensorPolicyRuntime", _FakeRuntime)
    monkeypatch.setattr(daemon, "_make_linucb_config", lambda _sensor: {"linucb": "cfg"})

    daemon._build_policy_runtime(
        sensor=SensorType.TEMP,
        alpha=0.5,
        tau=0.2,
        kbits=8,
        heartbeat_s=3.0,
        min_emit_ms=100,
        nominal_period_s=1.0,
    )

    assert captured["mode"] == PolicyMode.ADAPTIVE
    assert captured["linucb_cfg"] == {"linucb": "cfg"}
    assert captured["ewma_cfg"].diagnostics_enabled is False


def test_build_policy_runtime_adaptive_supports_bool_sensor_diag(monkeypatch, tmp_path) -> None:
    arms_cfg = {
        "arms": [{"tau": 0.3, "kbits": 8}],
        "diagnostics": {"enabled": False, "events_enabled": False},
        "sensors": {"temp": {"diagnostics": True}},
    }
    daemon = _make_daemon(monkeypatch, tmp_path, mode=PolicyMode.ADAPTIVE, arms_cfg=arms_cfg)

    captured = {}

    class _FakeRuntime:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(daemon_mod, "SensorPolicyRuntime", _FakeRuntime)
    monkeypatch.setattr(daemon, "_make_linucb_config", lambda _sensor: {"linucb": "cfg"})

    daemon._build_policy_runtime(
        sensor=SensorType.TEMP,
        alpha=0.5,
        tau=0.2,
        kbits=8,
        heartbeat_s=3.0,
        min_emit_ms=100,
        nominal_period_s=1.0,
    )

    assert captured["linucb_cfg"] == {"linucb": "cfg"}
    assert captured["ewma_cfg"].diagnostics_enabled is True


def test_build_policy_runtime_nonadaptive_skips_linucb_and_disables_diag(
    monkeypatch, tmp_path
) -> None:
    daemon = _make_daemon(
        monkeypatch,
        tmp_path,
        mode=PolicyMode.FIXED_TAU,
        arms_cfg={"diagnostics": True, "arms": [{"tau": 0.3, "kbits": 8}]},
    )
    captured = {}

    class _FakeRuntime:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(daemon_mod, "SensorPolicyRuntime", _FakeRuntime)
    monkeypatch.setattr(
        daemon,
        "_make_linucb_config",
        lambda _sensor: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    daemon._build_policy_runtime(
        sensor=SensorType.MIC_RMS,
        alpha=0.2,
        tau=3.0,
        kbits=6,
        heartbeat_s=None,
        min_emit_ms=0,
        nominal_period_s=0.1,
    )

    assert captured["mode"] == PolicyMode.FIXED_TAU
    assert captured["linucb_cfg"] is None
    assert captured["ewma_cfg"].diagnostics_enabled is False
