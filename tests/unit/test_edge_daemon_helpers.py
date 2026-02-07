from __future__ import annotations

import argparse
import random
import re

import pytest

from edge import edge_daemon as daemon_mod


def test_opt_path_normalization() -> None:
    assert daemon_mod._opt_path(None) is None
    assert daemon_mod._opt_path("") is None
    assert daemon_mod._opt_path("   ") is None
    assert daemon_mod._opt_path("none") is None
    assert daemon_mod._opt_path("Null") is None
    assert daemon_mod._opt_path(" configs/device.yaml ") == "configs/device.yaml"


def test_hb_none_normalizes_non_positive_and_invalid_values() -> None:
    assert daemon_mod._hb_none(None) is None
    assert daemon_mod._hb_none(0.0) is None
    assert daemon_mod._hb_none(-1.0) is None
    assert daemon_mod._hb_none("not-a-number") is None
    assert daemon_mod._hb_none(1.25) == 1.25


def test_parse_int_auto_accepts_base_prefix_and_rejects_invalid_literal() -> None:
    assert daemon_mod._parse_int_auto("42") == 42
    assert daemon_mod._parse_int_auto("0x2A") == 42
    assert daemon_mod._parse_int_auto("0b101010") == 42
    with pytest.raises(argparse.ArgumentTypeError):
        daemon_mod._parse_int_auto("forty-two")


def test_seed_everything_sets_deterministic_random_stream() -> None:
    daemon_mod._seed_everything(1234)
    a = [random.random() for _ in range(3)]
    daemon_mod._seed_everything(1234)
    b = [random.random() for _ in range(3)]
    assert a == b


def test_mk_run_dirs_and_default_run_dir_contract(tmp_path) -> None:
    run_dir = tmp_path / "nested" / "run"
    out = daemon_mod._mk_run_dirs(str(run_dir))
    assert out == str(run_dir)
    assert run_dir.exists()

    default_dir = daemon_mod._default_run_dir("dev1")
    norm = default_dir.replace("\\", "/")
    assert norm.startswith("artifacts/")
    assert norm.endswith("_dev1")
    assert re.match(r"^artifacts/\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z_dev1$", norm)


def test_load_policy_yaml_success_and_failure_mapping(monkeypatch) -> None:
    monkeypatch.setattr(
        daemon_mod,
        "load_policy_config_dict",
        lambda path: {"path": path, "ok": True},
    )
    assert daemon_mod._load_policy_yaml("configs/policy.yaml") == {
        "path": "configs/policy.yaml",
        "ok": True,
    }

    def _raise_policy(_path):
        raise RuntimeError("bad policy")

    monkeypatch.setattr(daemon_mod, "load_policy_config_dict", _raise_policy)
    with pytest.raises(SystemExit) as excinfo:
        daemon_mod._load_policy_yaml("configs/policy.yaml")
    assert excinfo.value.code == 2


def test_load_device_yaml_failure_mapping(monkeypatch) -> None:
    class _DevCfg:
        device_id = "dev1"

    monkeypatch.setattr(daemon_mod, "load_device_config", lambda path: _DevCfg())
    cfg = daemon_mod._load_device_yaml("configs/device.yaml")
    assert cfg.device_id == "dev1"

    def _raise_device(_path):
        raise RuntimeError("bad device")

    monkeypatch.setattr(daemon_mod, "load_device_config", _raise_device)
    with pytest.raises(SystemExit) as excinfo:
        daemon_mod._load_device_yaml("configs/device.yaml")
    assert excinfo.value.code == 2
