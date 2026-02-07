"""Compatibility wrappers for plotting generators split by domain."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from collector.plot_config import PlotConfig
from collector.plot_diagnostic import (
    _try_make_diagnostic_plots as _try_make_diagnostic_plots_impl,
)
from collector.plot_paper import _try_make_paper_plots as _try_make_paper_plots_impl
from collector.plot_pipeline import _try_make_pipeline_plots as _try_make_pipeline_plots_impl
from collector.plot_standard import _try_make_plots as _try_make_plots_impl


def _try_make_plots(
    out_dir: Path,
    summary: pd.DataFrame,
    *,
    plot_cfg: PlotConfig,
    pareto_p95: bool = False,
) -> list[Path]:
    return _try_make_plots_impl(
        out_dir,
        summary,
        plot_cfg=plot_cfg,
        pareto_p95=pareto_p95,
    )


def _try_make_pipeline_plots(
    out_dir: Path,
    *,
    events: pd.DataFrame,
    decisions_enriched: pd.DataFrame,
    by_run: pd.DataFrame,
    plot_cfg: PlotConfig,
) -> list[Path]:
    return _try_make_pipeline_plots_impl(
        out_dir,
        events=events,
        decisions_enriched=decisions_enriched,
        by_run=by_run,
        plot_cfg=plot_cfg,
    )


def _try_make_paper_plots(
    out_dir: Path,
    *,
    events: pd.DataFrame,
    decisions: pd.DataFrame,
    summary: pd.DataFrame,
    plot_cfg: PlotConfig,
    policy_config_path: str = "configs/policy.yaml",
    reward_window: int = 100,
    action_bins: int = 10,
    top_actions: int = 12,
    cellular_var_period_s: int = 30,
) -> list[Path]:
    return _try_make_paper_plots_impl(
        out_dir,
        events=events,
        decisions=decisions,
        summary=summary,
        plot_cfg=plot_cfg,
        policy_config_path=policy_config_path,
        reward_window=reward_window,
        action_bins=action_bins,
        top_actions=top_actions,
        cellular_var_period_s=cellular_var_period_s,
    )


def _try_make_diagnostic_plots(
    out_dir: Path,
    *,
    events: pd.DataFrame,
    decisions_enriched: pd.DataFrame,
    by_run: pd.DataFrame,
    summary: pd.DataFrame,
    arm_distribution: pd.DataFrame,
    entropy_windows: pd.DataFrame,
    plot_cfg: PlotConfig,
    arm_top_n: int = 12,
    entropy_smooth_window: int = 0,
    ucb_timeseries: bool = False,
) -> list[Path]:
    return _try_make_diagnostic_plots_impl(
        out_dir,
        events=events,
        decisions_enriched=decisions_enriched,
        by_run=by_run,
        summary=summary,
        arm_distribution=arm_distribution,
        entropy_windows=entropy_windows,
        plot_cfg=plot_cfg,
        arm_top_n=arm_top_n,
        entropy_smooth_window=entropy_smooth_window,
        ucb_timeseries=ucb_timeseries,
    )


__all__ = [
    "_try_make_diagnostic_plots",
    "_try_make_paper_plots",
    "_try_make_pipeline_plots",
    "_try_make_plots",
]

