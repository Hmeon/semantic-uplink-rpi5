from __future__ import annotations

import math

import pandas as pd
import pytest

from collector.quality_metrics import compute_seq_aligned_quality_metrics


def _base_row(
    *,
    run_id: str,
    profile: str = "slow_10kbps",
    policy: str,
    sensor: str = "temp",
    seq: int,
    ts: int,
    val: float,
    res: float,
    tau: float,
    kbits: int = 8,
    device_id: str = "dev1",
    meta_scenario: str | None = None,
    meta_seed: int | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "run_id": run_id,
        "profile": profile,
        "policy": policy,
        "sensor": sensor,
        "device_id": device_id,
        "seq": seq,
        "ts": ts,
        "val": val,
        "pred": val,
        "res": res,
        "tau": tau,
        "kbits": kbits,
    }
    if meta_scenario is not None:
        row["meta_scenario"] = meta_scenario
    if meta_seed is not None:
        row["meta_seed"] = meta_seed
    return row


def test_quality_metrics_picks_periodic_run_with_most_samples() -> None:
    rows = [
        _base_row(run_id="p_short", policy="periodic", seq=1, ts=1, val=100.0, res=0.0, tau=0.2),
        _base_row(run_id="p_short", policy="periodic", seq=2, ts=2, val=100.0, res=0.0, tau=0.2),
        _base_row(run_id="p_long", policy="periodic", seq=1, ts=1, val=0.0, res=0.0, tau=0.2),
        _base_row(run_id="p_long", policy="periodic", seq=2, ts=2, val=0.0, res=0.0, tau=0.2),
        _base_row(run_id="p_long", policy="periodic", seq=3, ts=3, val=0.0, res=0.0, tau=0.2),
        _base_row(run_id="a1", policy="adaptive", seq=1, ts=1, val=0.0, res=0.0, tau=0.2),
        _base_row(run_id="a1", policy="adaptive", seq=2, ts=2, val=0.0, res=0.0, tau=0.2),
        _base_row(run_id="a1", policy="adaptive", seq=3, ts=3, val=0.0, res=0.0, tau=0.2),
        _base_row(run_id="f1", policy="fixed_tau", seq=1, ts=1, val=0.0, res=0.0, tau=0.3),
    ]
    df = pd.DataFrame(rows).drop(columns=["kbits"])

    out = compute_seq_aligned_quality_metrics(df)
    row = out[out["run_id"].astype("string") == "a1"].iloc[0]
    assert float(row["recon_mae_mean"]) == pytest.approx(0.0, abs=1e-12)
    assert float(row["recon_mae_p95"]) == pytest.approx(0.0, abs=1e-12)


def test_quality_metrics_no_anomaly_segments_is_vacuous_recall_one() -> None:
    rows = [
        _base_row(run_id="p1", policy="periodic", seq=1, ts=1, val=1.0, res=0.1, tau=0.2),
        _base_row(run_id="p1", policy="periodic", seq=2, ts=2, val=1.0, res=0.2, tau=0.2),
        _base_row(run_id="a1", policy="adaptive", seq=1, ts=1, val=1.0, res=0.1, tau=0.2),
        _base_row(run_id="a1", policy="adaptive", seq=2, ts=2, val=1.0, res=0.2, tau=0.2),
        _base_row(run_id="f1", policy="fixed_tau", seq=1, ts=1, val=1.0, res=0.1, tau=1.0),
        _base_row(run_id="f1", policy="fixed_tau", seq=2, ts=2, val=1.0, res=0.2, tau=1.0),
    ]
    df = pd.DataFrame(rows).drop(columns=["kbits"])

    out = compute_seq_aligned_quality_metrics(df)
    row = out[out["run_id"].astype("string") == "a1"].iloc[0]
    assert float(row["anomaly_segments"]) == pytest.approx(0.0, abs=1e-12)
    assert float(row["anomaly_segments_hit"]) == pytest.approx(0.0, abs=1e-12)
    assert float(row["anomaly_segment_recall"]) == pytest.approx(1.0, abs=1e-12)


def test_quality_metrics_missing_tau_ref_policy_yields_nan_recall_fields() -> None:
    rows = [
        _base_row(run_id="p1", policy="periodic", seq=1, ts=1, val=1.0, res=0.5, tau=0.2),
        _base_row(run_id="p1", policy="periodic", seq=2, ts=2, val=1.0, res=0.6, tau=0.2),
        _base_row(run_id="a1", policy="adaptive", seq=1, ts=1, val=1.0, res=0.5, tau=0.2),
        _base_row(run_id="a1", policy="adaptive", seq=2, ts=2, val=1.0, res=0.6, tau=0.2),
    ]
    df = pd.DataFrame(rows)

    out = compute_seq_aligned_quality_metrics(df)
    row = out[out["run_id"].astype("string") == "a1"].iloc[0]
    assert math.isnan(float(row["anomaly_tau_ref"]))
    assert math.isnan(float(row["anomaly_segment_recall"]))


def test_quality_metrics_groups_by_meta_scenario_and_seed() -> None:
    rows = [
        _base_row(
            run_id="p_s1",
            policy="periodic",
            seq=1,
            ts=1,
            val=0.0,
            res=0.0,
            tau=0.2,
            meta_scenario="scnA",
            meta_seed=1,
        ),
        _base_row(
            run_id="p_s1",
            policy="periodic",
            seq=2,
            ts=2,
            val=0.0,
            res=0.0,
            tau=0.2,
            meta_scenario="scnA",
            meta_seed=1,
        ),
        _base_row(
            run_id="a_s1",
            policy="adaptive",
            seq=1,
            ts=1,
            val=0.0,
            res=0.0,
            tau=0.2,
            meta_scenario="scnA",
            meta_seed=1,
        ),
        _base_row(
            run_id="a_s1",
            policy="adaptive",
            seq=2,
            ts=2,
            val=0.0,
            res=0.0,
            tau=0.2,
            meta_scenario="scnA",
            meta_seed=1,
        ),
        _base_row(
            run_id="p_s2",
            policy="periodic",
            seq=1,
            ts=1,
            val=100.0,
            res=0.0,
            tau=0.2,
            meta_scenario="scnA",
            meta_seed=2,
        ),
        _base_row(
            run_id="p_s2",
            policy="periodic",
            seq=2,
            ts=2,
            val=100.0,
            res=0.0,
            tau=0.2,
            meta_scenario="scnA",
            meta_seed=2,
        ),
        _base_row(
            run_id="a_s2",
            policy="adaptive",
            seq=1,
            ts=1,
            val=100.0,
            res=0.0,
            tau=0.2,
            meta_scenario="scnA",
            meta_seed=2,
        ),
        _base_row(
            run_id="a_s2",
            policy="adaptive",
            seq=2,
            ts=2,
            val=100.0,
            res=0.0,
            tau=0.2,
            meta_scenario="scnA",
            meta_seed=2,
        ),
    ]
    df = pd.DataFrame(rows).drop(columns=["kbits"])

    out = compute_seq_aligned_quality_metrics(df)
    ad = out[out["policy"].astype("string") == "adaptive"].copy()
    assert len(ad) == 2
    assert float(ad["recon_mae_mean"].max()) == pytest.approx(0.0, abs=1e-12)
