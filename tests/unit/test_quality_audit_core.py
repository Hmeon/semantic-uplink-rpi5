from __future__ import annotations

import json
import struct
import zlib

import numpy as np
import pandas as pd
import pytest

from collector import quality_audit as qa


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    head = struct.pack(">I", len(data)) + chunk_type + data
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return head + struct.pack(">I", crc)


def _make_png_bytes(*, width: int, height: int, dpi: int = 300) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    xppm = int(round(float(dpi) / 0.0254))
    phys = struct.pack(">IIB", xppm, xppm, 1)
    row = b"\x00" + (b"\x00\x00\x00" * width)
    raw = row * height
    idat = zlib.compress(raw)
    return (
        sig
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"pHYs", phys)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def _make_png_bytes_no_phys(*, width: int, height: int) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + (b"\x00\x00\x00" * width)
    raw = row * height
    idat = zlib.compress(raw)
    return sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _prepare_analysis_with_figs(tmp_path):
    analysis = tmp_path / "analysis"
    figs = analysis / "figs"
    figs.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "rate_Bps": 1.0,
                "aoi_mean_ms": 1.0,
                "aoi_p95_ms": 1.0,
                "mae_event_mean": 1.0,
                "mae_event_p95": 1.0,
                "kbits_mean": 1.0,
            }
        ]
    ).to_csv(analysis / "metrics_summary.csv", index=False)
    (analysis / "analysis_meta.json").write_text(
        json.dumps({"flags": {"plots": False}, "plot_cfg": {"formats": ["png"], "dpi": 300}}),
        encoding="utf-8",
    )
    return analysis, figs


def test_discover_figs_dir_and_infer_formats(tmp_path) -> None:
    analysis = tmp_path / "analysis"
    figs = analysis / "figs"
    figs.mkdir(parents=True)
    (figs / "a.png").write_bytes(b"x")
    (figs / "a.pdf").write_bytes(b"x")

    found = qa._discover_figs_dir(analysis, preferred="figs")
    assert found == figs

    inferred = qa._infer_plot_formats(figs)
    assert set(inferred) == {"png", "pdf"}


def test_parse_png_info_reads_size_and_dpi(tmp_path) -> None:
    p = tmp_path / "t.png"
    p.write_bytes(_make_png_bytes(width=3, height=2, dpi=300))

    info = qa._parse_png_info(p)
    assert info["width_px"] == 3
    assert info["height_px"] == 2
    assert float(info["dpi_x"]) == pytest.approx(300.0, rel=1e-3)
    assert float(info["dpi_y"]) == pytest.approx(300.0, rel=1e-3)


def test_validate_figure_name_contract() -> None:
    ok, details = qa._validate_figure_name(
        "temp_slow_10kbps_adaptive_rate_bar__run01",
        allowed_sensors={"temp"},
        allowed_profiles={"slow 10kbps"},
        allowed_policies={"adaptive"},
    )
    assert ok is True
    assert details["metric"] == "rate_bar"
    assert details["run_id"] == "run01"

    bad, why = qa._validate_figure_name(
        "unknown_profile_adaptive_rate",
        allowed_sensors={"temp"},
        allowed_profiles={"slow 10kbps"},
        allowed_policies={"adaptive"},
    )
    assert bad is False
    assert "reason" in why


def test_table_metric_audit_group_statuses() -> None:
    df = pd.DataFrame(
        {
            "policy": [
                "periodic",
                "periodic",
                "fixed_tau",
                "fixed_tau",
                "adaptive",
                "adaptive",
            ],
            "rate_Bps": [1.0, 2.0, 1.0, np.nan, np.nan, np.nan],
            "aoi_mean_ms": [1.0, np.inf, 2.0, 3.0, np.nan, np.nan],
        }
    )

    out = qa._table_metric_audit(df, group_key="policy")
    assert out["rate_Bps"]["status"] == "FAIL"
    assert out["rate_Bps"]["by_group"]["periodic"]["status"] == "PASS"
    assert out["rate_Bps"]["by_group"]["fixed_tau"]["status"] == "FAIL"
    assert out["rate_Bps"]["by_group"]["adaptive"]["status"] == "SKIP"
    assert out["aoi_mean_ms"]["status"] == "FAIL"


def test_scan_print_and_except_traceback(tmp_path) -> None:
    p = tmp_path / "sample.py"
    p.write_text(
        """
def bad():
    try:
        x = 1
    except Exception:
        logger.error("missing traceback")

def good():
    try:
        x = 1
    except Exception:
        logger.exception("has traceback")

def with_print():
    print("debug")
""".strip(),
        encoding="utf-8",
    )

    prints = qa._scan_print_calls([p])
    assert len(prints) == 1
    assert prints[0]["path"].endswith("/sample.py")

    ex = qa._scan_except_without_traceback([p], allowlist_by_path=None)
    failures = ex["failures"]
    assert len(failures) == 1
    assert failures[0]["function"] == "bad"


def test_run_quality_audit_and_write_files_minimal(tmp_path) -> None:
    analysis = tmp_path / "analysis"
    analysis.mkdir(parents=True)
    pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "rate_Bps": 1.0,
                "aoi_mean_ms": 1.0,
                "aoi_p95_ms": 1.0,
                "mae_event_mean": 1.0,
                "mae_event_p95": 1.0,
                "kbits_mean": 1.0,
            }
        ]
    ).to_csv(analysis / "metrics_summary.csv", index=False)

    report = qa.run_quality_audit(
        analysis,
        figs_dir_name="figs",
        require_vector=False,
        code_roots=(),
    )
    assert "visualization" in report
    assert "tables" in report
    assert "logging" in report

    json_path, md_path = qa.write_quality_audit_files(report, analysis_dir=analysis)
    assert json_path.exists()
    assert md_path.exists()
    assert "Quality audit report" in md_path.read_text(encoding="utf-8")


def test_run_quality_audit_tiny_png_failure(tmp_path) -> None:
    analysis, figs = _prepare_analysis_with_figs(tmp_path)
    fname = "temp_slow_10kbps_compare_rate_bar.png"
    (figs / fname).write_bytes(b"x")

    report = qa.run_quality_audit(
        analysis,
        min_png_bytes=100,
        require_vector=False,
        code_roots=(),
    )
    assert fname in report["visualization"]["tiny_files"]


def test_run_quality_audit_small_png_dimensions_failure(tmp_path) -> None:
    analysis, figs = _prepare_analysis_with_figs(tmp_path)
    fname = "temp_slow_10kbps_compare_rate_bar.png"
    (figs / fname).write_bytes(_make_png_bytes(width=10, height=10, dpi=300))

    report = qa.run_quality_audit(
        analysis,
        min_png_bytes=1,
        min_png_width=20,
        min_png_height=20,
        require_vector=False,
        code_roots=(),
    )
    assert fname in report["visualization"]["small_png_dims"]


def test_run_quality_audit_missing_png_dpi_metadata_failure(tmp_path) -> None:
    analysis, figs = _prepare_analysis_with_figs(tmp_path)
    fname = "temp_slow_10kbps_compare_rate_bar.png"
    (figs / fname).write_bytes(_make_png_bytes_no_phys(width=60, height=40))

    report = qa.run_quality_audit(
        analysis,
        min_png_bytes=1,
        min_png_width=10,
        min_png_height=10,
        require_vector=False,
        code_roots=(),
    )
    assert fname in report["visualization"]["png_quality_fails"]


def test_run_quality_audit_low_png_dpi_failure(tmp_path) -> None:
    analysis, figs = _prepare_analysis_with_figs(tmp_path)
    fname = "temp_slow_10kbps_compare_rate_bar.png"
    (figs / fname).write_bytes(_make_png_bytes(width=60, height=40, dpi=72))

    report = qa.run_quality_audit(
        analysis,
        min_png_bytes=1,
        min_png_width=10,
        min_png_height=10,
        require_png_dpi=300,
        require_vector=False,
        code_roots=(),
    )
    assert fname in report["visualization"]["png_quality_fails"]


def test_run_quality_audit_naming_violation_failure(tmp_path) -> None:
    analysis, figs = _prepare_analysis_with_figs(tmp_path)
    bad_name = "badname.png"
    (figs / bad_name).write_bytes(_make_png_bytes(width=60, height=40, dpi=300))

    report = qa.run_quality_audit(
        analysis,
        min_png_bytes=1,
        min_png_width=10,
        min_png_height=10,
        require_vector=False,
        code_roots=(),
    )
    assert bad_name in report["visualization"]["naming_violations"]


def test_run_quality_audit_format_and_missing_expected_files(tmp_path) -> None:
    analysis = tmp_path / "analysis"
    figs = analysis / "figs"
    figs.mkdir(parents=True)

    pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "rate_Bps": 10.0,
                "aoi_mean_ms": 400.0,
                "aoi_p95_ms": 800.0,
                "mae_event_mean": 0.6,
                "mae_event_p95": 0.8,
                "kbits_mean": 8.0,
            }
        ]
    ).to_csv(analysis / "metrics_summary.csv", index=False)
    (analysis / "analysis_meta.json").write_text(
        json.dumps(
            {
                "flags": {
                    "plots": True,
                    "diagnostic_plots": False,
                    "ucb_timeseries": False,
                    "pareto_p95": True,
                },
                "plot_cfg": {"formats": ["png"], "dpi": 300},
            }
        ),
        encoding="utf-8",
    )

    only_one = qa.fig_basename(
        sensor="temp",
        profile="slow_10kbps",
        policy="compare",
        metric="rate_bar",
    )
    (figs / f"{only_one}.png").write_bytes(_make_png_bytes(width=80, height=60, dpi=300))

    report = qa.run_quality_audit(
        analysis,
        min_png_bytes=1,
        min_png_width=10,
        min_png_height=10,
        require_vector=True,
        code_roots=(),
    )
    assert report["format_audit"]["status"] == "FAIL"
    assert "vector format missing" in str(report["format_audit"]["reason"])
    assert int(report["visualization"]["expected_status_counts"].get("FAIL", 0)) >= 1
    assert report["visualization"]["missing_expected_files"]


def test_run_quality_audit_diagnostic_expected_scope_is_adaptive_only(tmp_path) -> None:
    analysis = tmp_path / "analysis"
    figs = analysis / "figs"
    figs.mkdir(parents=True)

    pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "temp",
                "rate_Bps": 10.0,
                "aoi_mean_ms": 400.0,
                "aoi_p95_ms": 800.0,
                "mae_event_mean": 0.6,
                "mae_event_p95": 0.8,
                "kbits_mean": 8.0,
                "linucb_safe_forced_rate": 0.5,
                "linucb_forced_reason_aoi_limit_rate": 0.2,
                "linucb_forced_reason_mae_limit_rate": 0.2,
                "linucb_forced_reason_both_rate": 0.1,
                "linucb_switch_rate": 0.3,
            }
        ]
    ).to_csv(analysis / "metrics_summary.csv", index=False)

    pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "temp",
                "event_reason_threshold_count": 3,
                "event_reason_heartbeat_count": 2,
                "linucb_rate_limit_skips_total": 1,
                "dup_bytes_ratio": 0.1,
            }
        ]
    ).to_csv(analysis / "metrics_by_run.csv", index=False)

    pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "temp",
                "arm_id": 0,
                "frac": 1.0,
            }
        ]
    ).to_csv(analysis / "linucb_arm_distribution.csv", index=False)

    pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "temp",
                "window_idx": 0,
                "window_s": 60,
                "entropy_log2": 0.0,
            }
        ]
    ).to_csv(analysis / "linucb_entropy_60s.csv", index=False)

    (analysis / "analysis_meta.json").write_text(
        json.dumps(
            {
                "flags": {
                    "plots": True,
                    "diagnostic_plots": True,
                    "ucb_timeseries": True,
                    "pareto_p95": False,
                },
                "plot_cfg": {"formats": ["png"], "dpi": 300},
            }
        ),
        encoding="utf-8",
    )

    report = qa.run_quality_audit(
        analysis,
        min_png_bytes=1,
        min_png_width=1,
        min_png_height=1,
        require_vector=False,
        code_roots=(),
    )
    expected = report["visualization"]["expected_figures"]
    assert not any("safe_forced_reasons" in str(x.get("base_name")) for x in expected)
    assert not any("switch_rate" in str(x.get("base_name")) for x in expected)
    assert not any("event_reasons" in str(x.get("base_name")) for x in expected)
    assert not any("arm_dist" in str(x.get("base_name")) for x in expected)
    assert not any("entropy_60s" in str(x.get("base_name")) for x in expected)


def test_write_quality_audit_files_renders_partial_failure_sections(tmp_path) -> None:
    analysis = tmp_path / "analysis"
    analysis.mkdir(parents=True)

    report = {
        "generated_at": "2026-02-07T00:00:00+00:00",
        "analysis_dir": str(analysis).replace("\\", "/"),
        "figs_dir": str(analysis / "figs").replace("\\", "/"),
        "plot_formats_inferred": ("png",),
        "plot_flags": {
            "plots_enabled": True,
            "diagnostic_plots_enabled": True,
            "ucb_timeseries_enabled": False,
            "pareto_p95_enabled": False,
        },
        "format_audit": {"status": "FAIL", "reason": "vector format missing (pdf/svg)"},
        "visualization": {
            "expected_figures": [{"status": "FAIL", "base_name": "temp_a_b_rate_bar"}],
            "expected_status_counts": {"FAIL": 1},
            "missing_expected_files": ["temp_a_b_rate_bar.png"],
            "file_checks": [{"status": "FAIL", "path": "figs/temp_a_b_rate_bar.png"}],
            "file_status_counts": {"FAIL": 1},
            "label_checks": [
                {
                    "base_name": "temp_a_b_rate_bar",
                    "status": "FAIL",
                    "details": {"missing": ["xlabel"]},
                }
            ],
            "label_status_counts": {"FAIL": 1},
            "naming_violations": ["badname.png"],
            "png_quality_fails": ["temp_a_b_rate_bar.png"],
            "tiny_files": ["tiny.png"],
            "small_png_dims": ["small.png"],
        },
        "tables": {
            "metric_coverage": {"metrics_summary.csv": {"rate_Bps": {"status": "FAIL"}}},
        },
        "logging": {
            "print_calls": [{"path": "collector/x.py", "line": 10}],
            "exception_traceback_audit": {
                "failures": [{"path": "collector/x.py", "line": 11, "function": "f"}],
                "skipped": 0,
            },
            "logging_setup_present": False,
            "timestamp_format_detected": False,
            "policy_diag_debug_log_audit": {
                "status": "FAIL",
                "details": {"missing_keys": ["run_id"]},
            },
            "status_counts": {"PASS": 0, "FAIL": 4},
        },
    }

    json_path, md_path = qa.write_quality_audit_files(report, analysis_dir=analysis)
    assert json_path.exists()
    assert md_path.exists()

    md = md_path.read_text(encoding="utf-8")
    assert "## Missing expected figures (FAIL)" in md
    assert "## Figure naming violations (FAIL)" in md
    assert "## PNG quality failures (FAIL)" in md
    assert "## Missing axis labels (FAIL)" in md
    assert "## Tiny figure files (FAIL)" in md
    assert "## Small PNG dimensions (FAIL)" in md
    assert "print(): FAIL" in md
    assert "policy diagnostics DEBUG log keys: FAIL missing=" in md
