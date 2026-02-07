from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from collector.plot_config import PlotConfig
from collector.plot_generators import (
    _try_make_diagnostic_plots,
    _try_make_paper_plots,
    _try_make_pipeline_plots,
    _try_make_plots,
)


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame()


def test_plot_generators_fallback_when_matplotlib_unavailable(monkeypatch, tmp_path: Path) -> None:
    import collector.plot_diagnostic as pdiag
    import collector.plot_paper as ppaper
    import collector.plot_pipeline as ppipeline
    import collector.plot_standard as pstd

    monkeypatch.setattr(pstd, "_maybe_import_matplotlib", lambda: (None, None))
    monkeypatch.setattr(ppipeline, "_maybe_import_matplotlib", lambda: (None, None))
    monkeypatch.setattr(ppaper, "_maybe_import_matplotlib", lambda: (None, None))
    monkeypatch.setattr(pdiag, "_maybe_import_matplotlib", lambda: (None, None))
    cfg = PlotConfig(dir_name="figs", formats=("png",), dpi=150)

    assert _try_make_plots(tmp_path, _empty_df(), plot_cfg=cfg, pareto_p95=True) == []
    assert _try_make_pipeline_plots(
        tmp_path,
        events=_empty_df(),
        decisions_enriched=_empty_df(),
        by_run=_empty_df(),
        plot_cfg=cfg,
    ) == []
    assert _try_make_paper_plots(
        tmp_path,
        events=_empty_df(),
        decisions=_empty_df(),
        summary=_empty_df(),
        plot_cfg=cfg,
    ) == []
    assert _try_make_diagnostic_plots(
        tmp_path,
        events=_empty_df(),
        decisions_enriched=_empty_df(),
        by_run=_empty_df(),
        summary=_empty_df(),
        arm_distribution=_empty_df(),
        entropy_windows=_empty_df(),
        plot_cfg=cfg,
    ) == []


def test_try_make_plots_filename_contract(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    summary = pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "temp/sensor:1|A B",
                "rate_Bps": 100.0,
                "aoi_mean_ms": 500.0,
                "aoi_p95_ms": 1000.0,
                "mae_event_mean": 1.0,
                "mae_event_p95": 1.1,
                "kbits_mean": 8.0,
            },
            {
                "profile": "slow_10kbps",
                "policy": "fixed_tau",
                "sensor": "temp/sensor:1|A B",
                "rate_Bps": 20.0,
                "aoi_mean_ms": 450.0,
                "aoi_p95_ms": 900.0,
                "mae_event_mean": 0.8,
                "mae_event_p95": 0.9,
                "kbits_mean": 8.0,
            },
            {
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp/sensor:1|A B",
                "rate_Bps": 10.0,
                "aoi_mean_ms": 420.0,
                "aoi_p95_ms": 880.0,
                "mae_event_mean": 0.6,
                "mae_event_p95": 0.7,
                "kbits_mean": 8.0,
            },
        ]
    )
    cfg = PlotConfig(dir_name="figs", formats=("png",), dpi=120)
    created = _try_make_plots(tmp_path, summary, plot_cfg=cfg, pareto_p95=False)

    assert created, "expected at least one figure to be generated"
    for p in created:
        assert isinstance(p, Path)
        assert p.suffix == ".png"
        assert p.parent == tmp_path / "figs"
        assert "/" not in p.name
        assert "\\" not in p.name
        assert ":" not in p.name
        assert "|" not in p.name


def test_try_make_pipeline_plots_dup_ratio_generation(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    by_run = pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "profile": "slow_10kbps",
                "policy": "periodic",
                "dup_bytes_ratio": 0.20,
            },
            {
                "run_id": "run-2",
                "profile": "slow_10kbps",
                "policy": "fixed_tau",
                "dup_bytes_ratio": 0.12,
            },
            {
                "run_id": "run-3",
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "dup_bytes_ratio": 0.05,
            },
        ]
    )
    cfg = PlotConfig(dir_name="figs", formats=("png",), dpi=120)
    created = _try_make_pipeline_plots(
        tmp_path,
        events=_empty_df(),
        decisions_enriched=_empty_df(),
        by_run=by_run,
        plot_cfg=cfg,
    )
    assert created
    assert any("dup_bytes_ratio" in p.name for p in created)


def test_try_make_paper_plots_summary_panel_generation(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    summary = pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "temp",
                "rate_Bps": 100.0,
                "aoi_mean_ms": 1200.0,
                "mae_event_mean": 1.2,
            },
            {
                "profile": "slow_10kbps",
                "policy": "fixed_tau",
                "sensor": "temp",
                "rate_Bps": 80.0,
                "aoi_mean_ms": 1000.0,
                "mae_event_mean": 1.0,
            },
            {
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "rate_Bps": 60.0,
                "aoi_mean_ms": 900.0,
                "mae_event_mean": 0.8,
            },
        ]
    )
    cfg = PlotConfig(dir_name="figs", formats=("png",), dpi=120)
    created = _try_make_paper_plots(
        tmp_path,
        events=_empty_df(),
        decisions=_empty_df(),
        summary=summary,
        plot_cfg=cfg,
    )
    assert created
    assert any("env_metrics_panel" in p.name for p in created)


def test_try_make_diagnostic_plots_switch_generation_and_adaptive_guard(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    cfg = PlotConfig(dir_name="figs", formats=("png",), dpi=120)

    summary_no_adaptive = pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "temp",
                "linucb_switch_rate": 0.2,
            }
        ]
    )
    created_empty = _try_make_diagnostic_plots(
        tmp_path,
        events=_empty_df(),
        decisions_enriched=_empty_df(),
        by_run=_empty_df(),
        summary=summary_no_adaptive,
        arm_distribution=_empty_df(),
        entropy_windows=_empty_df(),
        plot_cfg=cfg,
    )
    assert created_empty == []

    summary_adaptive = pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "linucb_switch_rate": 0.15,
            }
        ]
    )
    created = _try_make_diagnostic_plots(
        tmp_path,
        events=_empty_df(),
        decisions_enriched=_empty_df(),
        by_run=_empty_df(),
        summary=summary_adaptive,
        arm_distribution=_empty_df(),
        entropy_windows=_empty_df(),
        plot_cfg=cfg,
    )
    assert created
    assert any("switch_rate" in p.name for p in created)
