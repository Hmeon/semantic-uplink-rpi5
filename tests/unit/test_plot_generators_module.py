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
    import collector.plot_generators as pg

    monkeypatch.setattr(pg, "_maybe_import_matplotlib", lambda: (None, None))
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
