from __future__ import annotations

import pandas as pd

from collector import analyze as analyze_mod
from collector.kpi import compare_policies
from collector.reporting import write_report_md


def _summary_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "temp",
                "n_events": 100,
                "duration_s": 10.0,
                "rate_Bps": 100.0,
                "aoi_mean_ms": 500.0,
                "aoi_p95_ms": 1000.0,
                "mae_event_mean": 1.0,
                "mae_event_p95": 1.0,
                "kbits_mean": 2.0,
                "recon_mae_mean": 1.0,
                "recon_mae_p95": 1.0,
                "recon_mae_p99": 1.0,
                "recon_mae_max": 1.0,
                "anomaly_tau_ref": 0.5,
                "anomaly_segments": 10.0,
                "anomaly_segments_hit": 10.0,
                "anomaly_segment_recall": 1.0,
            },
            {
                "profile": "slow_10kbps",
                "policy": "fixed_tau",
                "sensor": "temp",
                "n_events": 60,
                "duration_s": 10.0,
                "rate_Bps": 20.0,
                "aoi_mean_ms": 450.0,
                "aoi_p95_ms": 900.0,
                "mae_event_mean": 0.5,
                "mae_event_p95": 0.5,
                "kbits_mean": 1.0,
                "recon_mae_mean": 0.5,
                "recon_mae_p95": 0.5,
                "recon_mae_p99": 0.6,
                "recon_mae_max": 0.8,
                "anomaly_tau_ref": 0.5,
                "anomaly_segments": 10.0,
                "anomaly_segments_hit": 10.0,
                "anomaly_segment_recall": 1.0,
            },
            {
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "n_events": 50,
                "duration_s": 10.0,
                "rate_Bps": 10.0,
                "aoi_mean_ms": 470.0,
                "aoi_p95_ms": 950.0,
                "mae_event_mean": 0.45,
                "mae_event_p95": 0.45,
                "kbits_mean": 0.8,
                "recon_mae_mean": 0.45,
                "recon_mae_p95": 0.45,
                "recon_mae_p99": 0.55,
                "recon_mae_max": 0.7,
                "anomaly_tau_ref": 0.5,
                "anomaly_segments": 10.0,
                "anomaly_segments_hit": 10.0,
                "anomaly_segment_recall": 0.95,
            },
        ]
    )


def test_write_report_md_creates_expected_sections(tmp_path) -> None:
    out_dir = tmp_path / "out"
    write_report_md(out_dir, _summary_frame())
    text = (out_dir / "report.md").read_text(encoding="utf-8")

    assert "| profile | policy | sensor | n_events |" in text
    assert "Quality (seq-aligned vs periodic)" in text
    assert "LinUCB/" in text


def test_analyze_wrapper_matches_reporting_output(tmp_path) -> None:
    summary = _summary_frame()
    comparisons = compare_policies(summary, baseline_policy="periodic")

    a = tmp_path / "a"
    b = tmp_path / "b"
    write_report_md(a, summary, comparisons=comparisons, baseline_policy="periodic")
    analyze_mod._write_report_md(b, summary, comparisons=comparisons, baseline_policy="periodic")

    ta = (a / "report.md").read_text(encoding="utf-8")
    tb = (b / "report.md").read_text(encoding="utf-8")
    assert ta == tb
    assert "Project verdict" in ta
    assert "KPI" in ta
