from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from collector.plot_config import PlotConfig
from collector.plot_orchestrator import generate_plots


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame()


def test_generate_plots_disabled_skips_all() -> None:
    calls: list[str] = []

    def _mk(*args, **kwargs):  # noqa: ANN001
        calls.append("called")
        return []

    figures, paper, diag = generate_plots(
        logger=logging.getLogger("plot-orchestrator-test"),
        out_dir=Path("."),
        events=_empty_df(),
        decisions=_empty_df(),
        decisions_enriched=_empty_df(),
        by_run=_empty_df(),
        summary=_empty_df(),
        arm_distribution=_empty_df(),
        entropy_windows=_empty_df(),
        plot_cfg=PlotConfig(),
        plots_enabled=False,
        pareto_p95=False,
        paper_plots_enabled=False,
        policy_config_path="configs/policy.yaml",
        reward_window=10,
        action_bins=12,
        top_actions=20,
        cellular_var_period_s=5,
        diagnostic_plots_enabled=False,
        arm_top_n=8,
        entropy_smooth_window=3,
        ucb_timeseries=False,
        try_make_plots=_mk,
        try_make_pipeline_plots=_mk,
        try_make_paper_plots=_mk,
        try_make_diagnostic_plots=_mk,
    )
    assert figures == []
    assert paper == []
    assert diag == []
    assert calls == []


def test_generate_plots_handles_optional_failures() -> None:
    calls: list[str] = []

    def _plots(out_dir, summary, *, plot_cfg, pareto_p95):  # noqa: ANN001
        calls.append("plots")
        return [Path("a.png")]

    def _pipeline(out_dir, *, events, decisions_enriched, by_run, plot_cfg):  # noqa: ANN001
        calls.append("pipeline")
        return [Path("b.png")]

    def _paper(*args, **kwargs):  # noqa: ANN001
        calls.append("paper")
        raise RuntimeError("paper failure")

    def _diag(*args, **kwargs):  # noqa: ANN001
        calls.append("diag")
        return [Path("c.png")]

    figures, paper, diag = generate_plots(
        logger=logging.getLogger("plot-orchestrator-test"),
        out_dir=Path("."),
        events=_empty_df(),
        decisions=_empty_df(),
        decisions_enriched=_empty_df(),
        by_run=_empty_df(),
        summary=_empty_df(),
        arm_distribution=_empty_df(),
        entropy_windows=_empty_df(),
        plot_cfg=PlotConfig(),
        plots_enabled=True,
        pareto_p95=True,
        paper_plots_enabled=True,
        policy_config_path="configs/policy.yaml",
        reward_window=10,
        action_bins=12,
        top_actions=20,
        cellular_var_period_s=5,
        diagnostic_plots_enabled=True,
        arm_top_n=8,
        entropy_smooth_window=3,
        ucb_timeseries=True,
        try_make_plots=_plots,
        try_make_pipeline_plots=_pipeline,
        try_make_paper_plots=_paper,
        try_make_diagnostic_plots=_diag,
    )
    assert figures == [Path("a.png"), Path("b.png")]
    assert paper == []
    assert diag == [Path("c.png")]
    assert calls == ["plots", "pipeline", "paper", "diag"]
