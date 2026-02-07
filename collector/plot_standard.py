"""Standard performance plot generators."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from collector.plot_config import PlotConfig
from collector.plot_generator_utils import (
    _apply_plot_style,
    _fig_basename,
    _maybe_import_matplotlib,
    _save_figure_multi,
)
from collector.plot_labels import (
    LABEL_AOI_MEAN_MS,
    LABEL_AOI_P95_MS,
    LABEL_COMPONENT,
    LABEL_POLICY,
    LABEL_RATE_BPS,
)


def _try_make_plots(
    out_dir: Path,
    summary: pd.DataFrame,
    *,
    plot_cfg: PlotConfig,
    pareto_p95: bool = False,
) -> list[Path]:
    """
    핵심 성능 지표 플롯 생성(논문/보고서용 품질).

    - profile×sensor별 policy 비교 bar + Pareto scatter
    - 저장: PNG(기본 300dpi) + 선택 벡터(PDF/SVG)
    """
    matplotlib, plt = _maybe_import_matplotlib()
    if matplotlib is None or plt is None:
        return []
    _apply_plot_style(matplotlib)

    figs_dir = out_dir / plot_cfg.dir_name

    policy_order = ["periodic", "fixed_tau", "adaptive"]
    colors = {"periodic": "#9CA3AF", "fixed_tau": "#2563EB", "adaptive": "#F59E0B"}
    markers = {"periodic": "o", "fixed_tau": "s", "adaptive": "^"}
    created: list[Path] = []

    def _maybe_log_y(ax, ys: np.ndarray) -> None:
        y = ys[np.isfinite(ys)]
        if y.size < 2:
            return
        y_pos = y[y > 0]
        if y_pos.size < 2:
            return
        ratio = float(np.nanmax(y_pos) / max(1e-12, float(np.nanmin(y_pos))))
        if ratio >= 10.0:
            ax.set_yscale("log")

    def _bar_compare(
        g: pd.DataFrame,
        *,
        metric: str,
        ylabel: str,
        title: str,
        base_name: str,
    ) -> None:
        if metric not in g.columns:
            return
        gg = g.copy()
        gg["policy"] = pd.Categorical(gg["policy"], categories=policy_order, ordered=True)
        gg = gg.sort_values("policy")
        xs = [str(x) for x in gg["policy"].tolist()]
        ys = pd.to_numeric(gg[metric], errors="coerce").to_numpy(dtype=np.float64)
        if not np.isfinite(ys).any():
            return
        err_col = f"{metric}_std"
        if err_col in gg.columns:
            yerr = (
                pd.to_numeric(gg[err_col], errors="coerce")
                .fillna(0.0)
                .to_numpy(dtype=np.float64)
            )
        else:
            yerr = np.zeros_like(ys)

        fig, ax = plt.subplots(figsize=(6.6, 3.8))
        ax.bar(
            xs,
            ys,
            yerr=yerr,
            capsize=4,
            color=[colors.get(x, "#6B7280") for x in xs],
        )
        ax.set_title(title)
        ax.set_xlabel(LABEL_POLICY)
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", alpha=0.25)
        ax.set_axisbelow(True)
        _maybe_log_y(ax, ys)
        fig.tight_layout()
        created.extend(_save_figure_multi(fig, figs_dir, base_name=base_name, cfg=plot_cfg))
        plt.close(fig)

    def _pareto_compare(
        g: pd.DataFrame,
        *,
        x_col: str,
        y_col: str,
        xlabel: str,
        ylabel: str,
        title: str,
        base_name: str,
    ) -> None:
        if x_col not in g.columns or y_col not in g.columns:
            return
        gg = g.copy()
        gg["policy"] = pd.Categorical(gg["policy"], categories=policy_order, ordered=True)
        gg = gg.sort_values("policy")
        xs = pd.to_numeric(gg[x_col], errors="coerce").to_numpy(dtype=np.float64)
        ys = pd.to_numeric(gg[y_col], errors="coerce").to_numpy(dtype=np.float64)
        if not (np.isfinite(xs).any() and np.isfinite(ys).any()):
            return

        fig, ax = plt.subplots(figsize=(6.6, 4.0))
        for _, r in gg.iterrows():
            pol = str(r["policy"])
            xv = float(pd.to_numeric(r.get(x_col), errors="coerce"))
            yv = float(pd.to_numeric(r.get(y_col), errors="coerce"))
            if not (math.isfinite(xv) and math.isfinite(yv)):
                continue
            ax.scatter(
                xv,
                yv,
                s=80,
                marker=markers.get(pol, "o"),
                color=colors.get(pol, "#6B7280"),
                label=pol,
                edgecolors="white",
                linewidths=0.8,
                zorder=3,
            )
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.grid(alpha=0.25)
        ax.set_axisbelow(True)
        handles, labels = ax.get_legend_handles_labels()
        uniq: dict[str, object] = {}
        for lab, h in zip(labels, handles):
            uniq.setdefault(lab, h)
        ax.legend(uniq.values(), uniq.keys(), loc="best", frameon=True)
        fig.tight_layout()
        created.extend(_save_figure_multi(fig, figs_dir, base_name=base_name, cfg=plot_cfg))
        plt.close(fig)

    # profile × sensor별로 bar + pareto 생성
    for (prof, sensor), g in summary.groupby(["profile", "sensor"], sort=False):
        prof_s = str(prof)
        sensor_s = str(sensor)
        title_base = f"{sensor_s} · {prof_s}"

        _bar_compare(
            g,
            metric="rate_Bps",
            ylabel=LABEL_RATE_BPS,
            title=f"{title_base} · Rate",
            base_name=_fig_basename(
                sensor=sensor_s, profile=prof_s, policy="compare", metric="rate_bar"
            ),
        )
        _bar_compare(
            g,
            metric="aoi_mean_ms",
            ylabel=LABEL_AOI_MEAN_MS,
            title=f"{title_base} · AoI mean",
            base_name=_fig_basename(
                sensor=sensor_s, profile=prof_s, policy="compare", metric="aoi_mean_bar"
            ),
        )
        _bar_compare(
            g,
            metric="aoi_p95_ms",
            ylabel=LABEL_AOI_P95_MS,
            title=f"{title_base} · AoI p95",
            base_name=_fig_basename(
                sensor=sensor_s, profile=prof_s, policy="compare", metric="aoi_p95_bar"
            ),
        )
        _bar_compare(
            g,
            metric="mae_event_mean",
            ylabel="MAE (event) mean [a.u.]",
            title=f"{title_base} · MAE (event) mean",
            base_name=_fig_basename(
                sensor=sensor_s, profile=prof_s, policy="compare", metric="mae_mean_bar"
            ),
        )
        _bar_compare(
            g,
            metric="mae_event_p95",
            ylabel="MAE (event) p95 [a.u.]",
            title=f"{title_base} · MAE (event) p95",
            base_name=_fig_basename(
                sensor=sensor_s, profile=prof_s, policy="compare", metric="mae_p95_bar"
            ),
        )
        _bar_compare(
            g,
            metric="kbits_mean",
            ylabel="Mean quantization bits k̄ [bits]",
            title=f"{title_base} · k̄",
            base_name=_fig_basename(
                sensor=sensor_s, profile=prof_s, policy="compare", metric="kbits_mean_bar"
            ),
        )

        # Reward component breakdown (report-ready; only if components exist)
        comp_cols = [
            ("linucb_reward_aoi_mean", "reward_aoi"),
            ("linucb_reward_mae_mean", "reward_mae"),
            ("linucb_reward_rate_mean", "reward_rate"),
        ]
        if all(c in g.columns for c, _ in comp_cols):
            mat = []
            for pol in policy_order:
                row = g[g["policy"].astype("string") == pol]
                if row.empty:
                    mat.append([float("nan")] * len(comp_cols))
                else:
                    r0 = row.iloc[0]
                    mat.append(
                        [
                            float(pd.to_numeric(r0.get(col), errors="coerce"))
                            for col, _ in comp_cols
                        ]
                    )
            vals = np.asarray(mat, dtype=np.float64)
            if np.isfinite(vals).any():
                xs = np.arange(len(comp_cols))
                width = 0.22
                fig, ax = plt.subplots(figsize=(7.6, 3.8))
                for j, pol in enumerate(policy_order):
                    ax.bar(
                        xs + (j - 1) * width,
                        vals[j, :],
                        width=width,
                        label=pol,
                        color=colors.get(pol, "#6B7280"),
                    )
                ax.axhline(0.0, color="#9CA3AF", linewidth=1.0)
                ax.set_xticks(xs)
                ax.set_xticklabels([lab for _, lab in comp_cols], rotation=0)
                ax.set_xlabel(LABEL_COMPONENT)
                ax.set_ylabel("Reward component [reward units]")
                ax.set_title(f"Reward components (mean) - {sensor_s}/{prof_s}")
                ax.grid(axis="y", alpha=0.25)
                ax.set_axisbelow(True)
                ax.legend(loc="best", frameon=True)
                fig.tight_layout()
                created.extend(
                    _save_figure_multi(
                        fig,
                        figs_dir,
                        base_name=_fig_basename(
                            sensor=sensor_s,
                            profile=prof_s,
                            policy="compare",
                            metric="reward_components_bar",
                        ),
                        cfg=plot_cfg,
                    )
                )
                plt.close(fig)

        _pareto_compare(
            g,
            x_col="rate_Bps",
            y_col="aoi_mean_ms",
            xlabel="Rate [B/s]",
            ylabel="AoI mean [ms]",
            title=f"{title_base} · Pareto (Rate vs AoI mean)",
            base_name=_fig_basename(
                sensor=sensor_s, profile=prof_s, policy="compare", metric="pareto_rate_vs_aoi_mean"
            ),
        )
        if bool(pareto_p95):
            _pareto_compare(
                g,
                x_col="rate_Bps",
                y_col="aoi_p95_ms",
                xlabel="Rate [B/s]",
                ylabel="AoI p95 [ms]",
                title=f"{title_base} · Pareto (Rate vs AoI p95)",
                base_name=_fig_basename(
                    sensor=sensor_s,
                    profile=prof_s,
                    policy="compare",
                    metric="pareto_rate_vs_aoi_p95",
                ),
            )

    return created



__all__ = ["_try_make_plots"]

