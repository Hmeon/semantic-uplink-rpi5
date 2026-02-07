from __future__ import annotations

import pandas as pd
import pandas.testing as pdt
import pytest

from collector import analyze as analyze_mod
from collector import kpi as kpi_mod


def _summary_by_run_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "run_id": "r1",
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "temp",
                "n_events": 10,
                "duration_s": 1.0,
                "rate_Bps": 100.0,
                "aoi_mean_ms": 500.0,
                "aoi_p95_ms": 900.0,
                "mae_event_mean": 1.0,
                "mae_event_p95": 1.2,
                "kbits_mean": 2.0,
                "n_samples_est": 10,
                "n_suppressed_est": 0,
            },
            {
                "run_id": "r2",
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "temp",
                "n_events": 12,
                "duration_s": 2.0,
                "rate_Bps": 120.0,
                "aoi_mean_ms": 550.0,
                "aoi_p95_ms": 950.0,
                "mae_event_mean": 1.1,
                "mae_event_p95": 1.3,
                "kbits_mean": 2.5,
                "n_samples_est": 12,
                "n_suppressed_est": 1,
            },
        ]
    )


def _summary_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "temp",
                "rate_Bps": 100.0,
                "aoi_mean_ms": 500.0,
                "aoi_p95_ms": 1000.0,
                "mae_event_mean": 1.0,
                "mae_event_p95": 1.0,
                "recon_mae_mean": 1.0,
                "recon_mae_p95": 1.0,
                "recon_mae_p99": 1.0,
                "recon_mae_max": 1.0,
                "anomaly_segment_recall": 1.0,
            },
            {
                "profile": "slow_10kbps",
                "policy": "fixed_tau",
                "sensor": "temp",
                "rate_Bps": 20.0,
                "aoi_mean_ms": 400.0,
                "aoi_p95_ms": 900.0,
                "mae_event_mean": 0.5,
                "mae_event_p95": 0.5,
                "recon_mae_mean": 0.5,
                "recon_mae_p95": 0.5,
                "recon_mae_p99": 0.6,
                "recon_mae_max": 0.8,
                "anomaly_segment_recall": 1.0,
            },
            {
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "rate_Bps": 10.0,
                "aoi_mean_ms": 420.0,
                "aoi_p95_ms": 950.0,
                "mae_event_mean": 0.45,
                "mae_event_p95": 0.45,
                "recon_mae_mean": 0.45,
                "recon_mae_p95": 0.45,
                "recon_mae_p99": 0.55,
                "recon_mae_max": 0.75,
                "anomaly_segment_recall": 0.95,
            },
        ]
    )


def test_summarize_aggregates_run_level_metrics() -> None:
    out = kpi_mod.summarize(_summary_by_run_frame())
    assert len(out) == 1
    row = out.iloc[0]
    assert int(row["n_runs"]) == 2
    assert int(row["n_events"]) == 22
    assert float(row["duration_s"]) == pytest.approx(1.5, abs=1e-12)
    assert float(row["duration_s_std"]) > 0.0
    assert int(row["n_samples_est"]) == 22
    assert int(row["n_suppressed_est"]) == 1


def test_summarize_raises_on_missing_required_columns() -> None:
    with pytest.raises(ValueError, match="missing columns for summarize"):
        kpi_mod.summarize(pd.DataFrame([{"profile": "p"}]))


def test_compare_policies_raises_on_missing_required_columns() -> None:
    with pytest.raises(ValueError, match="missing columns for compare_policies"):
        kpi_mod.compare_policies(pd.DataFrame([{"profile": "p"}]))


def test_analyze_wrappers_match_kpi_module_outputs() -> None:
    summary = _summary_frame()

    cmp_from_analyze = analyze_mod.compare_policies(summary, baseline_policy="periodic")
    cmp_from_kpi = kpi_mod.compare_policies(summary, baseline_policy="periodic")
    pdt.assert_frame_equal(cmp_from_analyze, cmp_from_kpi)

    kpi_from_analyze, project_pass_analyze = analyze_mod.compute_final_kpi(summary)
    kpi_from_kpi, project_pass_kpi = kpi_mod.compute_final_kpi(summary)
    pdt.assert_frame_equal(kpi_from_analyze, kpi_from_kpi)
    assert project_pass_analyze is project_pass_kpi
