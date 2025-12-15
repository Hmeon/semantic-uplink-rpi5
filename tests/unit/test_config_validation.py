from __future__ import annotations

from pathlib import Path

import pytest

from common.config import load_device_config, load_link_profiles_config, load_policy_config_dict
from common.schema import LinkProfile


def test_load_policy_config_dict_parses_repo_config() -> None:
    cfg = load_policy_config_dict("configs/policy.yaml")
    assert "arms" in cfg
    assert isinstance(cfg["arms"], list)
    assert cfg["arms"]


def test_load_policy_config_dict_rejects_invalid_yaml(tmp_path: Path) -> None:
    bad = tmp_path / "policy.yaml"
    bad.write_text("arms: [{tau: -1, kbits: 0}]\n", encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_policy_config_dict(bad)
    msg = str(exc.value)
    assert "invalid policy config" in msg
    assert ("tau" in msg) or ("kbits" in msg)


def test_load_device_config_parses_repo_config() -> None:
    cfg = load_device_config("configs/device.yaml")
    assert cfg.device_id
    assert cfg.mqtt.host
    assert cfg.sensors.mic is not None or cfg.sensors.temp is not None


def test_load_link_profiles_config_covers_link_profiles_enum() -> None:
    cfg = load_link_profiles_config("configs/link_profiles.yaml")
    keys = set(cfg.profiles.keys())
    for p in LinkProfile:
        assert p.value in keys
