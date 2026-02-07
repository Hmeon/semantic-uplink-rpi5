from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from collector.load_normalize import (
    enrich_decisions_with_events,
    infer_profile_policy_from_path,
    infer_run_id_from_path,
    load_collector_meta,
)


def test_infer_profile_policy_from_path_parses_scenario_name(tmp_path: Path) -> None:
    p = tmp_path / "runs" / "slow_10kbps__adaptive__rep00" / "logs" / "events.csv"
    p.parent.mkdir(parents=True)
    p.write_text("ts,seq\n1,1\n", encoding="utf-8")

    profile, policy = infer_profile_policy_from_path(p)
    assert profile == "slow_10kbps"
    assert policy == "adaptive"


def test_infer_profile_policy_from_path_invalid_returns_unknown(tmp_path: Path) -> None:
    p = tmp_path / "runs" / "bad_scenario_name" / "logs" / "events.csv"
    p.parent.mkdir(parents=True)
    p.write_text("ts,seq\n1,1\n", encoding="utf-8")

    profile, policy = infer_profile_policy_from_path(p)
    assert profile == "unknown"
    assert policy == "unknown"


def test_infer_run_id_from_path_handles_artifacts_and_custom_root(tmp_path: Path) -> None:
    p_artifacts = tmp_path / "artifacts" / "runA" / "logs" / "events.csv"
    p_custom = tmp_path / "batch01" / "scenarioX" / "logs" / "events.csv"
    p_artifacts.parent.mkdir(parents=True)
    p_custom.parent.mkdir(parents=True)
    p_artifacts.write_text("x", encoding="utf-8")
    p_custom.write_text("x", encoding="utf-8")

    assert infer_run_id_from_path(p_artifacts) == "runA"
    assert infer_run_id_from_path(p_custom) == "batch01/scenarioX"


def test_load_collector_meta_skips_malformed_json_and_computes_ratio(tmp_path: Path) -> None:
    good = tmp_path / "artifacts" / "runA" / "logs" / "collector_meta.json"
    bad = tmp_path / "artifacts" / "runB" / "logs" / "collector_meta.json"
    good.parent.mkdir(parents=True)
    bad.parent.mkdir(parents=True)
    good.write_text(
        '{"bytes_total_including_dups": 200, "dup_bytes_dropped": 50, "dup_messages_dropped": 3}',
        encoding="utf-8",
    )
    bad.write_text("{not-json", encoding="utf-8")

    out = load_collector_meta([tmp_path / "artifacts"])
    assert len(out) == 1
    row = out.iloc[0]
    assert str(row["run_id"]) == "runA"
    assert float(row["dup_bytes_ratio"]) == pytest.approx(0.25, abs=1e-12)
    assert float(row["dup_messages_dropped"]) == pytest.approx(3.0, abs=1e-12)


def test_enrich_decisions_with_events_backfills_join_and_run_level_defaults() -> None:
    decisions = pd.DataFrame(
        {
            "run_id": ["run1", "run1"],
            "device_id": ["dev1", "dev1"],
            "ts": [10, 11],
            "tau": [0.2, 0.2],
            "kbits": [8, 8],
            "state_res": [0.2, 0.1],
        }
    )
    events = pd.DataFrame(
        {
            "run_id": ["run1"],
            "device_id": ["dev1"],
            "ts": [10],
            "tau": [0.2],
            "kbits": [8],
            "res": [0.21],
            "sensor": ["temp"],
            "profile": ["slow_10kbps"],
            "policy": ["adaptive"],
            "seq": [7],
            "t_recv_ns": [999],
        }
    )

    out = enrich_decisions_with_events(decisions, events)
    assert list(out["sensor"].astype(str)) == ["temp", "temp"]
    assert list(out["profile"].astype(str)) == ["slow_10kbps", "slow_10kbps"]
    assert list(out["policy"].astype(str)) == ["adaptive", "adaptive"]
    assert int(out.loc[0, "t_recv_ns"]) == 999
    assert pd.isna(out.loc[1, "t_recv_ns"])
    for col in ("tau_key", "kbits_key", "res_key", "t_recv_ns_ev"):
        assert col not in out.columns
