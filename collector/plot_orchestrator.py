"""Orchestrate plot generation paths for collector.analyze."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from collector.plot_config import PlotConfig


def generate_plots(
    *,
    logger: logging.Logger,
    out_dir: Path,
    events: pd.DataFrame,
    decisions: pd.DataFrame,
    decisions_enriched: pd.DataFrame,
    by_run: pd.DataFrame,
    summary: pd.DataFrame,
    arm_distribution: pd.DataFrame,
    entropy_windows: pd.DataFrame,
    plot_cfg: PlotConfig,
    plots_enabled: bool,
    pareto_p95: bool,
    paper_plots_enabled: bool,
    policy_config_path: str,
    reward_window: int,
    action_bins: int,
    top_actions: int,
    cellular_var_period_s: int,
    diagnostic_plots_enabled: bool,
    arm_top_n: int,
    entropy_smooth_window: int,
    ucb_timeseries: bool,
    try_make_plots: Callable[..., list[Path]],
    try_make_pipeline_plots: Callable[..., list[Path]],
    try_make_paper_plots: Callable[..., list[Path]],
    try_make_diagnostic_plots: Callable[..., list[Path]],
) -> tuple[list[Path], list[Path], list[Path]]:
    """Generate standard/paper/diagnostic plots with guarded error handling."""
    figures: list[Path] = []
    paper_figures: list[Path] = []
    diag_figures: list[Path] = []

    if not plots_enabled:
        return figures, paper_figures, diag_figures

    figures = try_make_plots(
        out_dir,
        summary,
        plot_cfg=plot_cfg,
        pareto_p95=bool(pareto_p95),
    )
    figures.extend(
        try_make_pipeline_plots(
            out_dir,
            events=events,
            decisions_enriched=decisions_enriched,
            by_run=by_run,
            plot_cfg=plot_cfg,
        )
    )

    if paper_plots_enabled:
        try:
            paper_figures = try_make_paper_plots(
                out_dir,
                events=events,
                decisions=decisions,
                summary=summary,
                plot_cfg=plot_cfg,
                policy_config_path=str(policy_config_path),
                reward_window=int(reward_window),
                action_bins=int(action_bins),
                top_actions=int(top_actions),
                cellular_var_period_s=int(cellular_var_period_s),
            )
        except Exception:
            logger.exception("failed to generate paper plots")
            paper_figures = []

    if diagnostic_plots_enabled:
        try:
            diag_figures = try_make_diagnostic_plots(
                out_dir,
                events=events,
                decisions_enriched=decisions_enriched,
                by_run=by_run,
                summary=summary,
                arm_distribution=arm_distribution,
                entropy_windows=entropy_windows,
                plot_cfg=plot_cfg,
                arm_top_n=int(arm_top_n),
                entropy_smooth_window=int(entropy_smooth_window),
                ucb_timeseries=bool(ucb_timeseries),
            )
        except Exception:
            logger.exception("failed to generate diagnostic plots")
            diag_figures = []

    return figures, paper_figures, diag_figures


__all__ = ["generate_plots"]
