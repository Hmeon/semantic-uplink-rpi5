"""Shared helper utilities for collector plotting generators."""

from __future__ import annotations

from pathlib import Path

from collector.plot_config import PlotConfig
from collector.plot_runtime import apply_plot_style as _apply_plot_style_impl
from collector.plot_runtime import maybe_import_matplotlib as _maybe_import_matplotlib_impl
from collector.plotting_support import build_fig_basename as _fig_basename_impl
from collector.plotting_support import save_figure_multi as _save_figure_multi_impl


def _fig_basename(
    *,
    sensor: str,
    profile: str,
    policy: str,
    metric: str,
    run_id: str | None = None,
) -> str:
    return _fig_basename_impl(
        sensor=sensor,
        profile=profile,
        policy=policy,
        metric=metric,
        run_id=run_id,
    )


def _maybe_import_matplotlib():
    return _maybe_import_matplotlib_impl()


def _apply_plot_style(matplotlib) -> None:
    _apply_plot_style_impl(matplotlib)


def _save_figure_multi(fig, out_dir: Path, *, base_name: str, cfg: PlotConfig) -> list[Path]:
    return _save_figure_multi_impl(
        fig,
        out_dir,
        base_name=base_name,
        formats=cfg.formats,
        dpi=int(cfg.dpi),
    )


__all__ = [
    "_apply_plot_style",
    "_fig_basename",
    "_maybe_import_matplotlib",
    "_save_figure_multi",
]

