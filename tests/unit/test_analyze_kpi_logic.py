from __future__ import annotations

import math

import pandas as pd
import pytest

from collector.analyze import compare_policies, compute_final_kpi


def _summary_frame(
    *,
    anomaly_recall: float = 0.95,
    adaptive_recon_p95: float = 0.45,
) -> pd.DataFrame:
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
                "recon_mae_p95": adaptive_recon_p95,
                "recon_mae_p99": 0.55,
                "recon_mae_max": 0.75,
                "anomaly_segment_recall": anomaly_recall,
            },
        ]
    )


def test_compare_policies_computes_improvements_against_selected_baseline() -> None:
    summary = _summary_frame()
    cmp_periodic = compare_policies(summary, baseline_policy="periodic")

    row = cmp_periodic[
        (cmp_periodic["profile"] == "slow_10kbps")
        & (cmp_periodic["sensor"] == "temp")
        & (cmp_periodic["policy"] == "adaptive")
    ].iloc[0]

    # rate improvement vs periodic: (100 - 10) / 100 * 100 = 90%
    assert float(row["rate_Bps_improvement_pct"]) == pytest.approx(90.0, abs=1e-12)
    assert float(row["aoi_p95_ms_improvement_pct"]) == pytest.approx(5.0, abs=1e-12)


def test_compute_final_kpi_pass_case() -> None:
    kpi, project_pass = compute_final_kpi(_summary_frame())
    assert project_pass is True
    assert len(kpi) == 1
    row = kpi.iloc[0]
    assert row["overall"] == "PASS"
    assert row["kpi1_rate_vs_periodic"] == "PASS"
    assert row["kpi2_rate_vs_fixed_tau"] == "PASS"
    assert row["kpi3_recon_p95_vs_fixed_tau"] == "PASS"
    assert row["kpi4_anomaly_segment_recall"] == "PASS"
    assert row["kpi5_aoi_p95_vs_fixed_tau"] == "PASS"


def test_compute_final_kpi_fails_on_coverage_guardrail() -> None:
    kpi, project_pass = compute_final_kpi(_summary_frame(anomaly_recall=0.4))
    assert project_pass is False
    row = kpi.iloc[0]
    assert row["kpi4_anomaly_segment_recall"] == "FAIL"
    assert row["overall"] == "FAIL"


def test_compute_final_kpi_handles_zero_fixed_baseline_recon() -> None:
    summary = _summary_frame(adaptive_recon_p95=0.01)
    summary.loc[summary["policy"] == "fixed_tau", "recon_mae_p95"] = 0.0
    kpi, project_pass = compute_final_kpi(summary)
    assert project_pass is False
    row = kpi.iloc[0]
    # base≈0 and candidate>0 -> forced hard fail branch
    assert float(row["recon_mae_p95_improvement_vs_fixed_tau_pct"]) == -100.0
    assert row["kpi3_recon_p95_vs_fixed_tau"] == "FAIL"


def test_compute_final_kpi_without_adaptive_returns_false() -> None:
    summary = _summary_frame()
    summary = summary[summary["policy"] != "adaptive"].copy()
    kpi, project_pass = compute_final_kpi(summary)
    assert kpi.empty
    assert project_pass is False


def test_compare_policies_with_missing_baseline_yields_nan() -> None:
    summary = _summary_frame()
    summary = summary[summary["policy"] != "periodic"].copy()
    cmp_periodic = compare_policies(summary, baseline_policy="periodic")
    row = cmp_periodic[cmp_periodic["policy"] == "adaptive"].iloc[0]
    assert math.isnan(float(row["baseline_rate_Bps"]))
    assert math.isnan(float(row["rate_Bps_improvement_pct"]))
