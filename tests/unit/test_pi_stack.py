from __future__ import annotations

import sys
from pathlib import Path


def test_edge_cmd_includes_buttons_and_tc_by_default(tmp_path: Path) -> None:
    from stack.pi_stack import StackConfig, _build_edge_cmd

    cfg = StackConfig(
        run_dir=str(tmp_path / "run"),
        device_config="configs/device.yaml",
        policy_arms="configs/policy.yaml",
    )
    cmd = _build_edge_cmd(cfg)

    assert cmd[:3] == [sys.executable, "-m", "edge.edge_daemon"]
    assert "--buttons-enable" in cmd
    assert "--tc-apply-on-button" in cmd
    assert "--tc-apply-on-start" in cmd
    assert "--tc-iface" in cmd
    assert "lo" in cmd
    assert "--tc-profiles-config" in cmd


def test_edge_cmd_tc_disable_removes_tc_flags(tmp_path: Path) -> None:
    from stack.pi_stack import StackConfig, _build_edge_cmd

    cfg = StackConfig(
        run_dir=str(tmp_path / "run"),
        device_config="configs/device.yaml",
        policy_arms="configs/policy.yaml",
        tc_enable=False,
    )
    cmd = _build_edge_cmd(cfg)
    assert "--tc-apply-on-button" not in cmd
    assert "--tc-iface" not in cmd
    assert "--tc-profiles-config" not in cmd


def test_write_mosquitto_conf_is_ephemeral(tmp_path: Path) -> None:
    from stack.pi_stack import StackConfig, _write_mosquitto_conf

    cfg = StackConfig(
        run_dir=str(tmp_path / "run"),
        device_config="configs/device.yaml",
        policy_arms="configs/policy.yaml",
        broker_port=1883,
        mosquitto_listen_host="127.0.0.1",
    )
    conf = tmp_path / "mosquitto.conf"
    _write_mosquitto_conf(cfg, out_path=conf)

    text = conf.read_text(encoding="utf-8")
    assert "listener 1883 127.0.0.1" in text
    assert "persistence false" in text


def test_parse_args_supports_none_paths() -> None:
    from stack.pi_stack import parse_args

    cfg = parse_args(["--device-config", "none", "--policy-arms", "none"])
    assert cfg.device_config == "configs/device.yaml"
    assert cfg.policy_arms == "configs/policy.yaml"
