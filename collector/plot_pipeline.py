"""Pipeline-level plot generators."""

from __future__ import annotations

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
    LABEL_DUP_BYTES_RATIO_PCT,
    LABEL_E2E_LATENCY_MS,
    LABEL_OUTBOX_PENDING_COUNT,
    LABEL_POLICY,
)


def _try_make_pipeline_plots(
    out_dir: Path,
    *,
    events: pd.DataFrame,
    decisions_enriched: pd.DataFrame,
    by_run: pd.DataFrame,
    plot_cfg: PlotConfig,
) -> list[Path]:
    """
    Pipeline-level plots that should be produced whenever data exists:
    - Outbox backlog (pending count) time-series
    - Duplicate bytes ratio (bar, %)
    - E2E latency distribution (boxplot)
    """
    matplotlib, plt = _maybe_import_matplotlib()
    if matplotlib is None or plt is None:
        return []
    _apply_plot_style(matplotlib)

    figs_dir = out_dir / plot_cfg.dir_name
    figs_dir.mkdir(parents=True, exist_ok=True)

    policy_order = ["periodic", "fixed_tau", "adaptive"]
    colors = {"periodic": "#9CA3AF", "fixed_tau": "#2563EB", "adaptive": "#F59E0B"}
    created: list[Path] = []

    # ------------------- Outbox backlog time-series -------------------
    need_outbox = {"run_id", "profile", "policy", "state_q_len", "ts"}
    if not decisions_enriched.empty and need_outbox.issubset(decisions_enriched.columns):
        d = decisions_enriched.copy()
        tcol = "t_recv_ns" if "t_recv_ns" in d.columns and d["t_recv_ns"].notna().any() else "ts"
        for (run_id, prof, pol), g in d.groupby(
            ["run_id", "profile", "policy"], sort=False, observed=True
        ):
            gg = g.copy()
            gg[tcol] = pd.to_numeric(gg[tcol], errors="coerce")
            gg["state_q_len"] = pd.to_numeric(gg["state_q_len"], errors="coerce")
            gg = gg.dropna(subset=[tcol, "state_q_len"]).sort_values(tcol, kind="mergesort")
            if gg.empty:
                continue
            t0 = float(gg[tcol].iloc[0])
            t_s = (gg[tcol].to_numpy(dtype=np.float64) - t0) / 1e9
            q = gg["state_q_len"].to_numpy(dtype=np.float64)
            if not np.isfinite(q).any():
                continue

            fig, ax = plt.subplots(figsize=(8.6, 3.6))
            ax.plot(t_s, q, color="#111827", linewidth=1.8)
            ax.fill_between(t_s, 0.0, q, where=q > 0, color="#F59E0B", alpha=0.15)
            ax.set_title(
                f"Outbox pending (decision-time samples) | profile={prof} | run={run_id}"
            )
            ax.set_xlabel("time since run start [s]")
            ax.set_ylabel(LABEL_OUTBOX_PENDING_COUNT)
            ax.grid(alpha=0.25)
            ax.set_axisbelow(True)
            created.extend(
                _save_figure_multi(
                    fig,
                    figs_dir,
                    base_name=_fig_basename(
                        sensor="all",
                        profile=str(prof),
                        policy=str(pol),
                        metric="outbox_pending_ts",
                        run_id=str(run_id),
                    ),
                    cfg=plot_cfg,
                )
            )
            plt.close(fig)

    # ------------------- Duplicate bytes ratio -------------------
    need_dup = {"run_id", "profile", "policy", "dup_bytes_ratio"}
    if not by_run.empty and need_dup.issubset(by_run.columns):
        dr = (
            by_run[["run_id", "profile", "policy", "dup_bytes_ratio"]]
            .drop_duplicates(subset=["run_id", "profile", "policy"], keep="last", ignore_index=True)
            .copy()
        )
        for prof, g in dr.groupby("profile", sort=False, observed=True):
            means: list[float] = []
            stds: list[float] = []
            xs: list[str] = []
            for pol in policy_order:
                xs.append(pol)
                v = pd.to_numeric(
                    g[g["policy"].astype("string") == pol]["dup_bytes_ratio"], errors="coerce"
                ).dropna()
                if not v.empty and np.isfinite(v.to_numpy(dtype=np.float64)).any():
                    means.append(float(v.mean()) * 100.0)
                    stds.append(float(v.std(ddof=1)) * 100.0 if len(v) >= 2 else 0.0)
                else:
                    means.append(float("nan"))
                    stds.append(0.0)
            if not np.isfinite(np.asarray(means, dtype=np.float64)).any():
                continue

            fig, ax = plt.subplots(figsize=(6.8, 3.6))
            ax.bar(
                xs,
                means,
                yerr=stds,
                capsize=4,
                color=[colors.get(x, "#6B7280") for x in xs],
            )
            ax.set_ylim(0, 100)
            ax.set_xlabel(LABEL_POLICY)
            ax.set_ylabel(LABEL_DUP_BYTES_RATIO_PCT)
            ax.set_title(f"Duplicate bytes ratio (QoS1 de-dup) | profile={prof}")
            ax.grid(axis="y", alpha=0.25)
            ax.set_axisbelow(True)
            created.extend(
                _save_figure_multi(
                    fig,
                    figs_dir,
                    base_name=_fig_basename(
                        sensor="all",
                        profile=str(prof),
                        policy="compare",
                        metric="dup_bytes_ratio",
                    ),
                    cfg=plot_cfg,
                )
            )
            plt.close(fig)

    # ------------------- E2E latency (boxplot) -------------------
    need_ev = {"profile", "policy", "sensor", "ts", "t_recv_ns"}
    if not events.empty and need_ev.issubset(events.columns):
        ev = events.copy()
        ev = ev[ev["t_recv_ns"].notna()].copy()
        if not ev.empty:
            ev["rx_delay_ms"] = np.maximum(
                (ev["t_recv_ns"].astype("float64") - ev["ts"].astype("float64")) / 1e6,
                0.0,
            )
            for (prof, sensor), g in ev.groupby(["profile", "sensor"], sort=False, observed=True):
                data: list[np.ndarray] = []
                labels: list[str] = []
                for pol in policy_order:
                    gp = g[g["policy"].astype("string") == pol]
                    y = (
                        pd.to_numeric(gp["rx_delay_ms"], errors="coerce")
                        .dropna()
                        .to_numpy(dtype=np.float64)
                    )
                    if y.size == 0:
                        continue
                    data.append(y)
                    labels.append(pol)
                if not data:
                    continue

                fig, ax = plt.subplots(figsize=(7.4, 4.2))
                boxplot_kwargs = {
                    "showfliers": False,
                    "patch_artist": True,
                    "medianprops": {"color": "#111827", "linewidth": 1.8},
                    "boxprops": {"edgecolor": "#374151"},
                    "whiskerprops": {"color": "#374151"},
                    "capprops": {"color": "#374151"},
                }
                try:
                    bp = ax.boxplot(data, tick_labels=labels, **boxplot_kwargs)
                except TypeError:
                    # Older matplotlib uses "labels" instead of "tick_labels".
                    bp = ax.boxplot(data, labels=labels, **boxplot_kwargs)
                for box, lab in zip(bp.get("boxes", []), labels):
                    box.set_facecolor(colors.get(lab, "#E5E7EB"))
                    box.set_alpha(0.35)
                ax.set_xlabel(LABEL_POLICY)
                ax.set_ylabel(LABEL_E2E_LATENCY_MS)
                ax.set_title(f"E2E latency distribution | {sensor}/{prof}")
                ax.grid(axis="y", alpha=0.25)
                ax.set_axisbelow(True)
                created.extend(
                    _save_figure_multi(
                        fig,
                        figs_dir,
                        base_name=_fig_basename(
                            sensor=str(sensor),
                            profile=str(prof),
                            policy="compare",
                            metric="rx_delay_box",
                        ),
                        cfg=plot_cfg,
                    )
                )
                plt.close(fig)

    return created



__all__ = ["_try_make_pipeline_plots"]

