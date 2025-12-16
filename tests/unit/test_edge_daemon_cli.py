from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


def _write_device_yaml(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "device.yaml"
    path.write_text(textwrap.dedent(content).lstrip(), encoding="utf-8")
    return path


def test_parse_args_applies_device_yaml_defaults(tmp_path) -> None:
    from edge.edge_daemon import parse_args

    device_yaml = _write_device_yaml(
        tmp_path,
        """
        device_id: dev1
        mqtt:
          host: broker.local
          port: 1884
        sensors:
          temp:
            period_hz: 2.5
        ui:
          enabled: true
          backend: console
        """,
    )

    args = parse_args(
        ["--device-config", str(device_yaml), "--mode", "periodic", "--temp-backend", "mock"]
    )
    assert args.device_id == "dev1"
    assert args.broker == "broker.local"
    assert args.port == 1884
    assert args.mic_enable is False
    assert args.temp_enable is True
    assert args.temp_hz == 2.5
    assert args.ui_enable is True
    assert args.ui_kind == "console"


def test_parse_args_cli_overrides_device_yaml(tmp_path) -> None:
    from edge.edge_daemon import parse_args

    device_yaml = _write_device_yaml(
        tmp_path,
        """
        device_id: dev1
        mqtt:
          host: broker.local
          port: 1884
        sensors:
          temp:
            period_hz: 1.0
        """,
    )

    args = parse_args(
        [
            "--device-config",
            str(device_yaml),
            "--broker",
            "override.local",
            "--port",
            "1999",
            "--temp-backend",
            "mock",
        ]
    )
    assert args.broker == "override.local"
    assert args.port == 1999


def test_parse_args_disable_flags_override_device_yaml(tmp_path) -> None:
    from edge.edge_daemon import parse_args

    device_yaml = _write_device_yaml(
        tmp_path,
        """
        device_id: dev1
        mqtt:
          host: broker.local
        sensors:
          temp:
            period_hz: 1.0
        """,
    )

    args = parse_args(
        ["--device-config", str(device_yaml), "--temp-disable", "--temp-backend", "mock"]
    )
    assert args.temp_enable is False


def test_parse_args_requires_device_id_when_no_device_config() -> None:
    from edge.edge_daemon import parse_args

    with pytest.raises(SystemExit) as excinfo:
        parse_args([])
    assert excinfo.value.code == 2


def test_parse_args_device_config_none_is_supported() -> None:
    from edge.edge_daemon import parse_args

    args = parse_args(
        [
            "--device-config",
            "none",
            "--device-id",
            "dev1",
            "--temp-enable",
            "--temp-backend",
            "mock",
        ]
    )
    assert args.device_config is None
    assert args.device_id == "dev1"
