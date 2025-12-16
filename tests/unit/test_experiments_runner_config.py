from __future__ import annotations

import json
from pathlib import Path

from common.schema import LinkProfile, PolicyMode
from experiments.run_scenarios import Scenario, ScenarioRunner, build_scenarios, parse_args


def test_experiments_parse_args_uses_device_config_defaults(tmp_path: Path) -> None:
    device_yaml = tmp_path / "device.yaml"
    device_yaml.write_text(
        "\n".join(
            [
                "device_id: devx",
                "sensors:",
                "  temp:",
                "    period_hz: 2",
                "ui:",
                "  enabled: true",
                "  backend: console",
                "mqtt:",
                "  host: broker.example",
                "  port: 1884",
                "  base_topic: edge",
                "",
            ]
        ),
        encoding="utf-8",
    )

    run_root = tmp_path / "runs"
    plan = parse_args(
        [
            "--device-config",
            str(device_yaml),
            "--link-profiles-config",
            "configs/link_profiles.yaml",
            "--run-root",
            str(run_root),
            "--modes",
            "periodic",
            "--profiles",
            "slow_10kbps",
        ]
    )

    assert plan.device_id == "devx"
    assert plan.broker == "broker.example"
    assert plan.port == 1884
    assert plan.use_mic is False
    assert plan.use_temp is True
    assert plan.temp_hz == 2.0
    assert plan.device_config == str(device_yaml)
    assert plan.link_profiles_config == "configs/link_profiles.yaml"

    scenarios = build_scenarios(plan)
    assert scenarios

    root = scenarios[0].out_dir.parent
    run_meta = json.loads((root / "run_meta.json").read_text(encoding="utf-8"))
    assert run_meta["configs"]["device_yaml"]["path"] == str(device_yaml)
    assert run_meta["configs"]["device_yaml"]["exists"] is True
    expected_link_path = str(Path("configs/link_profiles.yaml"))
    assert run_meta["configs"]["link_profiles_yaml"]["path"] == expected_link_path
    assert run_meta["configs"]["link_profiles_yaml"]["exists"] is True


def test_experiments_edge_cmd_disables_ui_and_respects_sensor_flags(tmp_path: Path) -> None:
    plan = parse_args(
        [
            "--device-id",
            "dev1",
            "--no-mic",
            "--temp",
            "--run-root",
            str(tmp_path / "runs"),
            "--modes",
            "periodic",
            "--profiles",
            "slow_10kbps",
            "--device-config",
            "configs/device.yaml",
            "--link-profiles-config",
            "configs/link_profiles.yaml",
        ]
    )

    runner = ScenarioRunner(plan)
    sc = Scenario(
        profile=LinkProfile.SLOW_10KBPS,
        mode=PolicyMode.PERIODIC,
        name="slow_10kbps__periodic",
        out_dir=tmp_path / "out",
    )
    cmd = runner._edge_cmd(sc)

    assert "--ui-disable" in cmd
    assert "--buttons-disable" in cmd
    assert "--device-config" in cmd

    # mic disabled → explicit override
    assert "--mic-disable" in cmd
    assert "--mic-enable" not in cmd

    # temp enabled → explicit override
    assert "--temp-enable" in cmd
    assert "--temp-disable" not in cmd
