"""Plot generator implementations extracted from collector.analyze."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from collector.load_normalize import enrich_decisions_with_events
from collector.plot_config import PlotConfig
from collector.plot_labels import (
    LABEL_AOI_MEAN_MS,
    LABEL_AOI_P95_MS,
    LABEL_ARM,
    LABEL_COMPONENT,
    LABEL_DUP_BYTES_RATIO_PCT,
    LABEL_E2E_LATENCY_MS,
    LABEL_LINK_PROFILE,
    LABEL_OUTBOX_PENDING_COUNT,
    LABEL_POLICY,
    LABEL_RATE_BPS,
    LABEL_UCB_TERM,
)
from collector.plot_runtime import apply_plot_style as _apply_plot_style_impl
from collector.plot_runtime import maybe_import_matplotlib as _maybe_import_matplotlib_impl
from collector.plotting_support import build_fig_basename as _fig_basename_impl
from collector.plotting_support import save_figure_multi as _save_figure_multi_impl
from common.config import load_policy_config_dict
from common.schema import LinkProfile


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


def _load_policy_yaml(path: str) -> dict:
    try:
        return load_policy_config_dict(path)
    except FileNotFoundError:
        return {}


def _format_action(tau: float, kbits: int) -> str:
    # τ는 소수점 3자리 정도면 그림/표에서 가독성이 좋다.
    return f"τ={float(tau):.3g}, k={int(kbits)}"


def _reconstruct_linucb_trace(
    decisions: pd.DataFrame,
    *,
    lambda_ridge: float = 1.0,
    aoi_scale_ms: float = 1000.0,
    res_scale: float | None = None,
    resvar_scale: float | None = None,
    qlen_scale: float = 50.0,
) -> pd.DataFrame:
    """
    decisions 로그로부터 LinUCB의 θ(팔별 선형모델) 업데이트를 재구성한다.

    - 업데이트 규칙(엣지 코드와 동일):
        A ← A + x x^T
        b ← b + r x
        θ = A^{-1} b
    - 반환은 "chosen arm의 θ(업데이트 후)"와 "counts 가중 평균 θ"를 포함한다.
    - regret는 per-step optimal을 직접 계산하기 어렵기 때문에,
      현재 모델의 예측값(θ^T x) 기반 proxy regret를 제공한다.
    """
    if decisions.empty:
        return pd.DataFrame()

    d = decisions.copy()
    time_col = "t_recv_ns" if "t_recv_ns" in d.columns and d["t_recv_ns"].notna().any() else "ts"
    d = d.sort_values(time_col, kind="mergesort").reset_index(drop=True)

    arms = (
        d[["tau", "kbits"]]
        .dropna()
        .drop_duplicates()
        .assign(tau_key=lambda x: x["tau"].astype("float64").round(6))
        .sort_values(["tau_key", "kbits"])
    )
    arm_keys = [(float(r["tau_key"]), int(r["kbits"])) for _, r in arms.iterrows()]
    if not arm_keys:
        return pd.DataFrame()

    tau_max = max(abs(t) for t, _k in arm_keys)
    rs = float(res_scale) if res_scale is not None else max(1e-9, float(tau_max))
    rvs = float(resvar_scale) if resvar_scale is not None else max(1e-9, rs * rs)

    d_dim = 6  # [bias, aoi, res, res_var, loss, q_len]
    a_mats = [np.eye(d_dim, dtype=np.float64) * float(lambda_ridge) for _ in arm_keys]
    b = [np.zeros((d_dim,), dtype=np.float64) for _ in arm_keys]
    theta = [np.zeros((d_dim,), dtype=np.float64) for _ in arm_keys]
    counts = [0 for _ in arm_keys]

    theta_wsum = np.zeros((d_dim,), dtype=np.float64)
    total = 0
    regret_cum = 0.0
    t0 = float(d[time_col].iloc[0])

    def _arm_idx(tau: float, kbits: int) -> int:
        key = (float(float(tau).__round__(6)), int(kbits))
        try:
            return arm_keys.index(key)
        except ValueError:
            # 로그 float 오차가 남는 경우: 같은 kbits 내 τ가 가장 가까운 arm
            best = 0
            best_d = 1e100
            for i, (t, k) in enumerate(arm_keys):
                if int(k) != int(kbits):
                    continue
                dist = abs(float(t) - float(tau))
                if dist < best_d:
                    best, best_d = i, dist
            return best

    def _context(row: dict) -> np.ndarray:
        aoi_n = float(row["state_aoi"]) / max(1e-9, float(aoi_scale_ms))
        res_n = abs(float(row["state_res"])) / max(1e-9, rs)
        resv_n = max(0.0, float(row["state_res_var"])) / max(1e-9, rvs)
        loss = float(min(1.0, max(0.0, float(row["state_loss"]))))
        qn = max(0.0, float(row["state_q_len"])) / max(1e-9, float(qlen_scale))
        return np.array([1.0, aoi_n, res_n, resv_n, loss, qn], dtype=np.float64)

    rows = []
    for step, r in enumerate(d.itertuples(index=False)):
        row = r._asdict()
        x = _context(row)
        chosen_i = _arm_idx(float(row["tau"]), int(row["kbits"]))

        # predicted regret (θ^T x)
        preds = [float(np.dot(theta[i], x)) for i in range(len(theta))]
        best_pred = max(preds) if preds else float("nan")
        chosen_pred = float(preds[chosen_i]) if preds else float("nan")
        regret = float(best_pred - chosen_pred) if math.isfinite(best_pred) else float("nan")
        if math.isfinite(regret):
            regret_cum += regret

        reward = float(row["reward"])

        # update
        a_old = a_mats[chosen_i]
        b_old = b[chosen_i]
        theta_old = theta[chosen_i]
        count_old = counts[chosen_i]

        a_new = a_old + np.outer(x, x)
        b_new = b_old + reward * x
        try:
            theta_new = np.linalg.solve(a_new, b_new)
        except np.linalg.LinAlgError:
            theta_new = theta_old

        a_mats[chosen_i] = a_new
        b[chosen_i] = b_new
        theta[chosen_i] = theta_new
        counts[chosen_i] = count_old + 1

        # counts 가중 평균 θ
        theta_wsum += (counts[chosen_i] * theta_new) - (count_old * theta_old)
        total += 1
        theta_avg = (theta_wsum / total) if total > 0 else np.full((d_dim,), np.nan)

        t_s = (float(row.get(time_col, row["ts"])) - t0) / 1e9
        rows.append(
            {
                "step": int(step),
                "t_s": float(t_s),
                "tau": float(row["tau"]),
                "kbits": int(row["kbits"]),
                "action": _format_action(float(row["tau"]), int(row["kbits"])),
                "reward": reward,
                "pred_reward": chosen_pred,
                "pred_reward_best": best_pred,
                "regret_pred": regret,
                "regret_pred_cum": float(regret_cum),
                "theta_bias": float(theta_new[0]),
                "theta_aoi": float(theta_new[1]),
                "theta_res": float(theta_new[2]),
                "theta_res_var": float(theta_new[3]),
                "theta_loss": float(theta_new[4]),
                "theta_q_len": float(theta_new[5]),
                "theta_avg_bias": float(theta_avg[0]),
                "theta_avg_aoi": float(theta_avg[1]),
                "theta_avg_res": float(theta_avg[2]),
                "theta_avg_res_var": float(theta_avg[3]),
                "theta_avg_loss": float(theta_avg[4]),
                "theta_avg_q_len": float(theta_avg[5]),
            }
        )
    return pd.DataFrame(rows)


def _detect_convergence_step(
    y: np.ndarray,
    *,
    window: int,
    eps: float,
    sustain: int,
) -> int | None:
    """moving average가 eps 이하로 안정화되는 최초 인덱스(휴리스틱)."""
    if y.size < max(2 * window, sustain + 1) or window <= 1:
        return None
    s = pd.Series(y).rolling(window=window, min_periods=window).mean().to_numpy()
    d = np.abs(s - np.roll(s, window))
    d[:window] = np.nan
    ok = np.isfinite(d) & (d <= eps)
    run = 0
    for i, v in enumerate(ok):
        if v:
            run += 1
            if run >= sustain:
                return int(i)
        else:
            run = 0
    return None


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
    """
    논문/최종 보고서용 추가 플롯 생성.
    - Feature Weight Convergence
    - Sensor value/residual vs Action distribution
    - Average Reward over Time
    - Cumulative Regret (predicted proxy)
    - Training stability proxy (|res| rolling mean)
    - Annotated timeline (representative run)
    - Environment comparison (Reward-by-profile, grouped bars)
    """
    matplotlib, plt = _maybe_import_matplotlib()
    if matplotlib is None or plt is None:
        return []
    _apply_plot_style(matplotlib)

    figs_dir = out_dir / plot_cfg.dir_name
    figs_dir.mkdir(parents=True, exist_ok=True)

    created: list[Path] = []

    # 0) 정책 비교 grouped bar (Rate/AoI/MAE) — sensor별
    if not summary.empty and {"profile", "policy", "sensor"}.issubset(summary.columns):
        policy_order = ["periodic", "fixed_tau", "adaptive"]
        colors = {"periodic": "#9CA3AF", "fixed_tau": "#2563EB", "adaptive": "#F59E0B"}
        metrics = [
            ("rate_Bps", "Rate [B/s] (↓)"),
            ("aoi_mean_ms", "AoI mean [ms] (↓)"),
            ("mae_event_mean", "MAE_event mean (↓)"),
        ]
        for sensor, g0 in summary.groupby("sensor", sort=False):
            fig, axes = plt.subplots(nrows=len(metrics), ncols=1, figsize=(8.6, 8.6), sharex=True)
            if len(metrics) == 1:
                axes = [axes]
            profiles = sorted({str(p) for p in g0["profile"].unique()})
            x = np.arange(len(profiles))
            width = 0.22
            for ax, (mcol, ylabel) in zip(axes, metrics):
                for j, pol in enumerate(policy_order):
                    gp = g0[g0["policy"] == pol]
                    ys = []
                    yerr = []
                    for prof in profiles:
                        row = gp[gp["profile"] == prof]
                        if row.empty:
                            ys.append(np.nan)
                            yerr.append(0.0)
                        else:
                            ys.append(float(row.iloc[0][mcol]))
                            std_col = f"{mcol}_std"
                            yerr.append(
                                float(row.iloc[0].get(std_col, 0.0))
                                if std_col in row.columns
                                else 0.0
                            )
                    ax.bar(
                        x + (j - 1) * width,
                        ys,
                        width=width,
                        label=pol,
                        color=colors.get(pol, "#6B7280"),
                        yerr=yerr,
                        capsize=3,
                    )
                ax.set_ylabel(ylabel)
                ax.grid(axis="y", alpha=0.25)
            axes[-1].set_xticks(x)
            axes[-1].set_xticklabels(profiles, rotation=0)
            axes[-1].set_xlabel(LABEL_LINK_PROFILE)
            axes[0].set_title(f"Policy comparison by profile · sensor={sensor}")
            axes[0].legend(loc="best", frameon=True)
            fig.tight_layout()
            created.extend(
                _save_figure_multi(
                    fig,
                    figs_dir,
                    base_name=_fig_basename(
                        sensor=str(sensor),
                        profile="all",
                        policy="compare",
                        metric="env_metrics_panel",
                    ),
                    cfg=plot_cfg,
                )
            )
            plt.close(fig)

    # 1) Sensor value/residual vs action distribution — events 기반 heatmap
    if not events.empty and {"profile", "sensor", "tau", "kbits", "res"}.issubset(events.columns):
        ev = events.copy()
        if "policy" in ev.columns:
            ev = ev[ev["policy"].astype("string") == "adaptive"]
        if not ev.empty:
            for (prof, sensor), g in ev.groupby(["profile", "sensor"], sort=False):
                g = g.copy()
                g["abs_res"] = g["res"].abs()
                try:
                    g["bin"] = pd.qcut(g["abs_res"], q=action_bins, duplicates="drop")
                except Exception:
                    mn = float(g["abs_res"].min())
                    mx = float(g["abs_res"].max())
                    if not math.isfinite(mn) or not math.isfinite(mx) or mn == mx:
                        continue
                    g["bin"] = pd.cut(
                        g["abs_res"],
                        bins=np.linspace(mn, mx, int(action_bins) + 1),
                        include_lowest=True,
                    )
                g["action"] = g.apply(lambda r: _format_action(r["tau"], int(r["kbits"])), axis=1)
                top = g["action"].value_counts().head(max(3, int(top_actions))).index.tolist()
                g.loc[~g["action"].isin(top), "action"] = "other"
                order = top + (["other"] if "other" in g["action"].unique() else [])
                pivot = (
                    g.pivot_table(
                        index="action",
                        columns="bin",
                        values="seq",
                        aggfunc="count",
                        fill_value=0,
                        observed=False,
                    ).loc[order]
                )
                fig, ax = plt.subplots(figsize=(10.0, 4.8))
                im = ax.imshow(pivot.to_numpy(), aspect="auto", cmap="viridis")
                ax.set_yticks(np.arange(pivot.shape[0]))
                ax.set_yticklabels(pivot.index.tolist())
                ax.set_xticks(np.arange(pivot.shape[1]))
                ax.set_xticklabels(
                    [str(c) for c in pivot.columns.tolist()],
                    rotation=35,
                    ha="right",
                )
                ax.set_xlabel("|residual| bin")
                ax.set_ylabel("chosen action (τ,k)")
                ax.set_title(
                    f"Action distribution vs |residual| · profile={prof} · "
                    f"sensor={sensor}"
                )
                cbar = fig.colorbar(im, ax=ax)
                cbar.set_label("count")
                fig.tight_layout()
                created.extend(
                    _save_figure_multi(
                        fig,
                        figs_dir,
                        base_name=_fig_basename(
                            sensor=str(sensor),
                            profile=str(prof),
                            policy="adaptive",
                            metric="action_heatmap",
                        ),
                        cfg=plot_cfg,
                    )
                )
                plt.close(fig)

    # decisions가 없으면 여기서 종료(나머지는 reward/regret/θ 필요)
    if decisions.empty:
        return created

    dec = enrich_decisions_with_events(decisions, events)
    dec = dec[np.isfinite(dec["reward"].astype("float64"))].copy()
    if dec.empty:
        return created

    cfg = _load_policy_yaml(policy_config_path)
    safety = cfg.get("safety") or {}
    aoi_max_ms = float(safety.get("aoi_max_ms", 5000.0))
    mae_max = float(safety.get("mae_max", 2.0))

    # 2) Environment comparison: Reward over time by profile (sensor별 패널)
    present_profiles = set(dec["profile"].astype(str).unique())
    prof_order = [p.value for p in LinkProfile if p.value in present_profiles]
    if not prof_order:
        prof_order = sorted(present_profiles)
    for sensor, ds in dec.groupby("sensor", sort=False):
        fig, axes = plt.subplots(
            nrows=1,
            ncols=len(prof_order),
            figsize=(5.0 * len(prof_order), 4.2),
            sharey=True,
        )
        if len(prof_order) == 1:
            axes = [axes]
        for ax, prof in zip(axes, prof_order):
            g = ds[ds["profile"] == prof].copy()
            if g.empty:
                ax.set_title(f"{prof} (no data)")
                ax.grid(alpha=0.25)
                continue
            series = []
            for run_id, gr in g.groupby("run_id", sort=False):
                gr = gr.sort_values("ts", kind="mergesort").reset_index(drop=True)
                y = gr["reward"].astype("float64").to_numpy()
                y_ma = (
                    pd.Series(y)
                    .rolling(window=reward_window, min_periods=max(3, reward_window // 4))
                    .mean()
                )
                series.append(y_ma.to_numpy())
                ax.plot(y_ma.to_numpy(), color="#9CA3AF", alpha=0.25, linewidth=1.0)
            max_len = max(len(s) for s in series)
            mat = np.full((len(series), max_len), np.nan, dtype=np.float64)
            for i, s in enumerate(series):
                mat[i, : len(s)] = s
            valid = np.isfinite(mat)
            n = valid.sum(axis=0).astype("int64")
            mu = np.where(n > 0, np.nansum(mat, axis=0) / np.maximum(1, n), np.nan)
            if mat.shape[0] >= 2:
                # 표본 표준편차(ddof=1): n<=1 구간은 0으로 처리
                mu2 = np.where(n > 0, mu, 0.0)
                var = np.where(
                    n > 1,
                    np.nansum((mat - mu2) ** 2, axis=0) / np.maximum(1, n - 1),
                    0.0,
                )
                sd = np.sqrt(np.maximum(0.0, var))
            else:
                sd = np.zeros_like(mu)
            ax.plot(mu, color="#111827", linewidth=2.2, label="mean")
            ax.fill_between(
                np.arange(len(mu)),
                mu - sd,
                mu + sd,
                color="#111827",
                alpha=0.12,
                label="±1σ",
            )
            ax.set_title(prof)
            ax.set_xlabel("decision step")
            ax.grid(alpha=0.25)
        axes[0].set_ylabel(f"reward (moving avg, window={reward_window})")
        fig.suptitle(f"Reward over time by profile · sensor={sensor}")
        fig.tight_layout()
        created.extend(
            _save_figure_multi(
                fig,
                figs_dir,
                base_name=_fig_basename(
                    sensor=str(sensor),
                    profile="all",
                    policy="adaptive",
                    metric="reward_by_profile_ts",
                ),
                cfg=plot_cfg,
            )
        )
        plt.close(fig)

    # 3) Representative run plots (profile×sensor별 1개)
    for (prof, sensor), g in dec.groupby(["profile", "sensor"], sort=False):
        run_counts = g["run_id"].value_counts()
        rep_run = str(run_counts.index[0])
        gr = g[g["run_id"] == rep_run].copy()
        if gr.empty:
            continue

        trace = _reconstruct_linucb_trace(gr)
        if trace.empty:
            continue

        # 3-1) Feature weight convergence (θ_avg)
        fig, ax = plt.subplots(figsize=(9.8, 4.6))
        for col, label in [
            ("theta_avg_aoi", "AoI"),
            ("theta_avg_res", "Residual"),
            ("theta_avg_res_var", "Residual variance"),
            ("theta_avg_loss", "Loss"),
            ("theta_avg_q_len", "Queue length"),
        ]:
            ax.plot(trace["step"], trace[col], linewidth=1.8, label=label)
        ax.set_title(f"Feature weight convergence (LinUCB θ, weighted avg) · {prof}/{sensor}")
        ax.set_xlabel("decision step")
        ax.set_ylabel("weight value")
        ax.grid(alpha=0.25)
        ax.legend(loc="best", frameon=True)
        fig.tight_layout()
        created.extend(
            _save_figure_multi(
                fig,
                figs_dir,
                base_name=_fig_basename(
                    sensor=str(sensor),
                    profile=str(prof),
                    policy="adaptive",
                    metric="feature_weights",
                    run_id=str(rep_run),
                ),
                cfg=plot_cfg,
            )
        )
        plt.close(fig)

        # 3-2) Reward over time + convergence marker
        y = trace["reward"].astype("float64").to_numpy()
        y_ma = (
            pd.Series(y)
            .rolling(window=reward_window, min_periods=max(3, reward_window // 4))
            .mean()
            .to_numpy()
        )
        y_top = float(np.nanmax(y_ma)) if np.isfinite(y_ma).any() else float(np.nanmax(y))
        # warmup end(탐색 구간 종료) 휴리스틱: 관측된 action을 1회 이상 모두 시도한 시점
        warmup_end = None
        seen = {}
        n_arms = int(trace[["tau", "kbits"]].drop_duplicates().shape[0])
        for i, a in enumerate(trace["action"].tolist()):
            seen[a] = seen.get(a, 0) + 1
            if len(seen) >= n_arms:
                warmup_end = i
                break
        conv = _detect_convergence_step(
            y_ma,
            window=max(5, reward_window // 2),
            eps=0.01,
            sustain=50,
        )
        fig, ax = plt.subplots(figsize=(9.8, 4.4))
        ax.plot(trace["step"], y, color="#9CA3AF", alpha=0.35, linewidth=1.0, label="reward")
        ax.plot(
            trace["step"],
            y_ma,
            color="#111827",
            linewidth=2.2,
            label=f"moving avg (w={reward_window})",
        )
        ax.axvline(0, color="#2563EB", linestyle="--", linewidth=1.2)
        ax.text(0, y_top, "start", color="#2563EB", fontsize=9, va="bottom")
        if warmup_end is not None:
            ax.axvline(warmup_end, color="#10B981", linestyle="--", linewidth=1.2)
            ax.text(
                warmup_end,
                y_top,
                "warmup done",
                color="#10B981",
                fontsize=9,
                va="bottom",
            )
        if conv is not None:
            ax.axvline(conv, color="#F59E0B", linestyle="--", linewidth=1.2)
            ax.text(conv, y_top, "converge*", color="#F59E0B", fontsize=9, va="bottom")
        ax.set_title(f"Reward over time (representative) · {rep_run} · {prof}/{sensor}")
        ax.set_xlabel("decision step")
        ax.set_ylabel("reward")
        ax.grid(alpha=0.25)
        ax.legend(loc="best", frameon=True)
        fig.tight_layout()
        created.extend(
            _save_figure_multi(
                fig,
                figs_dir,
                base_name=_fig_basename(
                    sensor=str(sensor),
                    profile=str(prof),
                    policy="adaptive",
                    metric="reward_ts",
                    run_id=str(rep_run),
                ),
                cfg=plot_cfg,
            )
        )
        plt.close(fig)

        # 3-3) Cumulative regret (proxy)
        fig, ax = plt.subplots(figsize=(9.8, 4.2))
        ax.plot(trace["step"], trace["regret_pred_cum"], color="#111827", linewidth=2.2)
        ax.set_title(f"Cumulative Regret (predicted proxy) · {rep_run} · {prof}/{sensor}")
        ax.set_xlabel("decision step")
        ax.set_ylabel("Cumulative Regret")
        ax.grid(alpha=0.25)
        fig.tight_layout()
        created.extend(
            _save_figure_multi(
                fig,
                figs_dir,
                base_name=_fig_basename(
                    sensor=str(sensor),
                    profile=str(prof),
                    policy="adaptive",
                    metric="cumulative_regret",
                    run_id=str(rep_run),
                ),
                cfg=plot_cfg,
            )
        )
        plt.close(fig)

        # 3-4) Stability: rolling |res|
        evr = events.copy()
        evr = evr[
            (evr["run_id"] == rep_run) & (evr["profile"] == prof) & (evr["sensor"] == sensor)
        ].copy()
        if not evr.empty and "res" in evr.columns:
            if "t_recv_ns" in evr.columns and evr["t_recv_ns"].notna().any():
                tcol = "t_recv_ns"
            else:
                tcol = "ts"
            evr = evr.sort_values(tcol, kind="mergesort").reset_index(drop=True)
            abs_res = evr["res"].astype("float64").abs().to_numpy()
            abs_ma = (
                pd.Series(abs_res)
                .rolling(window=max(10, reward_window // 2), min_periods=5)
                .mean()
                .to_numpy()
            )
            fig, ax = plt.subplots(figsize=(9.8, 3.8))
            ax.plot(abs_res, color="#9CA3AF", alpha=0.25, linewidth=1.0, label="|res|")
            ax.plot(abs_ma, color="#111827", linewidth=2.0, label="rolling mean")
            ax.set_title(f"Predictor stability proxy (|residual|) · {rep_run} · {prof}/{sensor}")
            ax.set_xlabel("event index")
            ax.set_ylabel("|residual|")
            ax.grid(alpha=0.25)
            ax.legend(loc="best", frameon=True)
            fig.tight_layout()
            created.extend(
                _save_figure_multi(
                    fig,
                    figs_dir,
                    base_name=_fig_basename(
                        sensor=str(sensor),
                        profile=str(prof),
                        policy="adaptive",
                        metric="stability_abs_res_ts",
                        run_id=str(rep_run),
                    ),
                    cfg=plot_cfg,
                )
            )
            plt.close(fig)

        # 3-5) Timeline with events: reward + AoI@rx + action changes + safety
        if not evr.empty and ("t_recv_ns" in evr.columns and evr["t_recv_ns"].notna().any()):
            evr = evr.sort_values("t_recv_ns", kind="mergesort").reset_index(drop=True)
            gen = evr["ts"].astype("int64").to_numpy()
            recv = evr["t_recv_ns"].astype("int64").to_numpy()
            gen_eff = np.maximum.accumulate(gen)
            aoi_ms = np.maximum((recv - gen_eff).astype("float64") / 1e6, 0.0)
            t0_ns = float(recv[0])
            t_s = (recv.astype("float64") - t0_ns) / 1e9

            gr_t = gr.copy()
            if "t_recv_ns" in gr_t.columns and gr_t["t_recv_ns"].notna().any():
                d_tcol = "t_recv_ns"
            else:
                d_tcol = "ts"
            gr_t = gr_t.sort_values(d_tcol, kind="mergesort").reset_index(drop=True)
            # 같은 기준(t0_ns)으로 time-align (reward/AoI 축 일치)
            dt_s = (gr_t[d_tcol].astype("float64").to_numpy() - t0_ns) / 1e9
            reward_t = gr_t["reward"].astype("float64").to_numpy()
            actions = gr_t.apply(
                lambda r: _format_action(r["tau"], int(r["kbits"])),
                axis=1,
            ).tolist()
            tau_min = float(gr_t["tau"].min())
            k_max = int(gr_t["kbits"].max())
            safe_action = _format_action(tau_min, k_max)
            safe_mask = [
                (actions[i] == safe_action)
                and (
                    float(gr_t.iloc[i]["state_aoi"]) >= aoi_max_ms
                    or abs(float(gr_t.iloc[i]["state_res"])) >= mae_max
                )
                for i in range(len(actions))
            ]

            fig, (ax1, ax2) = plt.subplots(nrows=2, ncols=1, figsize=(12.0, 6.4), sharex=True)
            ax1.plot(dt_s, reward_t, color="#111827", linewidth=1.8)
            ax1.set_ylabel("reward")
            ax1.set_title(f"Annotated timeline · {rep_run} · {prof}/{sensor}")
            ax1.grid(alpha=0.25)
            # cellular_var 링크 토글(근사): period로 수직선 표시
            if str(prof) == "cellular_var" and int(cellular_var_period_s) > 0:
                tmax = float(np.nanmax([np.nanmax(dt_s), np.nanmax(t_s)]))
                for k in range(1, int(tmax // float(cellular_var_period_s)) + 1):
                    xline = k * float(cellular_var_period_s)
                    ax1.axvline(xline, color="#10B981", linestyle=":", linewidth=1.0, alpha=0.55)

            # action change markers
            for i in range(1, len(actions)):
                if actions[i] != actions[i - 1]:
                    ax1.axvline(dt_s[i], color="#9CA3AF", linestyle="--", linewidth=1.0, alpha=0.7)
            # warmup end marker
            if warmup_end is not None and warmup_end < len(dt_s):
                ax1.axvline(
                    dt_s[warmup_end],
                    color="#10B981",
                    linestyle="--",
                    linewidth=1.2,
                    alpha=0.8,
                )
                ax1.text(
                    dt_s[warmup_end],
                    float(np.nanmax(reward_t)) if np.isfinite(reward_t).any() else 0.0,
                    "warmup done",
                    color="#10B981",
                    fontsize=9,
                    va="bottom",
                )
            # safety markers
            if any(safe_mask):
                xs = [dt_s[i] for i, m in enumerate(safe_mask) if m]
                ys = [reward_t[i] for i, m in enumerate(safe_mask) if m]
                ax1.scatter(xs, ys, s=40, color="#EF4444", label="safety (approx)")
                ax1.legend(loc="best", frameon=True)

            ax2.plot(t_s, aoi_ms, color="#2563EB", linewidth=1.8)
            ax2.set_xlabel("time [s]")
            ax2.set_ylabel("AoI@rx [ms]")
            ax2.grid(alpha=0.25)
            if str(prof) == "cellular_var" and int(cellular_var_period_s) > 0:
                tmax = float(np.nanmax(t_s))
                for k in range(1, int(tmax // float(cellular_var_period_s)) + 1):
                    xline = k * float(cellular_var_period_s)
                    ax2.axvline(xline, color="#10B981", linestyle=":", linewidth=1.0, alpha=0.55)

            fig.tight_layout()
            created.extend(
                _save_figure_multi(
                    fig,
                    figs_dir,
                    base_name=_fig_basename(
                        sensor=str(sensor),
                        profile=str(prof),
                        policy="adaptive",
                        metric="timeline",
                        run_id=str(rep_run),
                    ),
                    cfg=plot_cfg,
                )
            )
            plt.close(fig)

    return created


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
    """LinUCB/파이프라인 진단 플롯 생성(데이터가 없으면 자동 스킵)."""
    matplotlib, plt = _maybe_import_matplotlib()
    if matplotlib is None or plt is None:
        return []
    _apply_plot_style(matplotlib)

    figs_dir = out_dir / plot_cfg.dir_name
    figs_dir.mkdir(parents=True, exist_ok=True)

    policy_order = ["periodic", "fixed_tau", "adaptive"]
    colors = {"periodic": "#9CA3AF", "fixed_tau": "#2563EB", "adaptive": "#F59E0B"}
    created: list[Path] = []

    # NOTE: 아래 블록들은 데이터/컬럼이 없으면 조용히 skip 한다.

    # ------------------- (B1) Arm 선택 분포 -------------------
    if not arm_distribution.empty and {"run_id", "profile", "sensor", "arm_id", "frac"}.issubset(
        arm_distribution.columns
    ):
        ad = arm_distribution.copy()
        if "policy" in ad.columns:
            ad = ad[ad["policy"].astype("string") == "adaptive"]

        arm_meta = pd.DataFrame()
        need_meta = {"run_id", "profile", "sensor", "arm_id", "tau", "kbits"}
        if not decisions_enriched.empty and need_meta.issubset(decisions_enriched.columns):
            dm = decisions_enriched.dropna(subset=["arm_id"]).copy()
            if "policy" in dm.columns:
                dm = dm[dm["policy"].astype("string") == "adaptive"]
            if not dm.empty:
                dm["arm_id"] = pd.to_numeric(dm["arm_id"], errors="coerce").astype("Int64")
                arm_meta = (
                    dm.dropna(subset=["arm_id"])
                    .groupby(["run_id", "profile", "sensor", "arm_id"], observed=True, sort=False)
                    .agg({"tau": "median", "kbits": "median"})
                    .reset_index()
                )

        if not arm_meta.empty:
            ad = ad.merge(arm_meta, how="left", on=["run_id", "profile", "sensor", "arm_id"])

        for (run_id, prof, sensor), g in ad.groupby(
            ["run_id", "profile", "sensor"], sort=False, observed=True
        ):
            gg = g.copy()
            gg["frac"] = pd.to_numeric(gg["frac"], errors="coerce")
            gg = gg.dropna(subset=["frac"]).sort_values("frac", ascending=False, kind="mergesort")
            if gg.empty:
                continue

            top_n = int(arm_top_n)
            if top_n > 0 and len(gg) > top_n:
                head = gg.head(top_n).copy()
                tail = gg.iloc[top_n:].copy()
                n_decisions_max = 0
                if "n_decisions" in head.columns:
                    n_decisions_max = int(
                        pd.to_numeric(head["n_decisions"], errors="coerce").fillna(0).max()
                    )
                count_others = 0
                if "count" in tail.columns:
                    count_others = int(
                        pd.to_numeric(tail["count"], errors="coerce").fillna(0).sum()
                    )
                frac_others = 0.0
                if "frac" in tail.columns:
                    frac_others = float(
                        pd.to_numeric(tail["frac"], errors="coerce").fillna(0.0).sum()
                    )
                others = {
                    "run_id": str(run_id),
                    "profile": str(prof),
                    "sensor": str(sensor),
                    "arm_id": -1,
                    "count": count_others,
                    "frac": frac_others,
                    "n_decisions": n_decisions_max,
                    "tau": float("nan"),
                    "kbits": float("nan"),
                }
                gg = pd.concat([head, pd.DataFrame([others])], ignore_index=True)

            def _arm_label(r: pd.Series) -> str:
                arm_id = int(r.get("arm_id", -1))
                if arm_id < 0:
                    return "others"
                tau = r.get("tau", None)
                kbits = r.get("kbits", None)
                if tau is not None and kbits is not None:
                    try:
                        tau_f = float(tau)
                        kb_i = int(float(kbits))
                    except Exception:
                        return f"arm{arm_id}"
                    if math.isfinite(tau_f):
                        return f"arm{arm_id}: τ={tau_f:g}s, k={kb_i}"
                return f"arm{arm_id}"

            gg["label"] = gg.apply(_arm_label, axis=1)
            gg["pct"] = (gg["frac"].astype("float64") * 100.0).clip(lower=0.0)
            gg = gg.sort_values("pct", ascending=True, kind="mergesort")

            fig_h = max(3.4, 0.35 * len(gg) + 1.6)
            fig, ax = plt.subplots(figsize=(7.6, fig_h))
            ax.barh(gg["label"].tolist(), gg["pct"].tolist(), color=colors["adaptive"])
            ax.set_xlabel("Arm selection fraction [%]")
            ax.set_ylabel(LABEL_ARM)
            ax.set_title(f"Arm selection distribution · {sensor}/{prof} · run={run_id}")
            ax.grid(axis="x", alpha=0.25)
            ax.set_axisbelow(True)
            for y, v in enumerate(gg["pct"].tolist()):
                if math.isfinite(float(v)):
                    ax.text(float(v) + 0.6, y, f"{float(v):.1f}%", va="center", fontsize=9)
            fig.tight_layout()
            created.extend(
                _save_figure_multi(
                    fig,
                    figs_dir,
                    base_name=_fig_basename(
                        sensor=str(sensor),
                        profile=str(prof),
                        policy="adaptive",
                        metric="arm_dist",
                        run_id=str(run_id),
                    ),
                    cfg=plot_cfg,
                )
            )
            plt.close(fig)

    # ------------------- (B2) Action entropy (time-series) -------------------
    need_entropy = {"run_id", "profile", "sensor", "window_idx", "entropy_log2"}
    if not entropy_windows.empty and need_entropy.issubset(entropy_windows.columns):
        ew = entropy_windows.copy()
        if "policy" in ew.columns:
            ew = ew[ew["policy"].astype("string") == "adaptive"]

        smooth_w = int(entropy_smooth_window)

        for (run_id, prof, sensor), g in ew.groupby(
            ["run_id", "profile", "sensor"], sort=False, observed=True
        ):
            gg = g.copy()
            win_s = 60
            if "window_s" in gg.columns and gg["window_s"].notna().any():
                try:
                    win_s_raw = pd.to_numeric(gg["window_s"], errors="coerce").dropna()
                    win_s = int(float(win_s_raw.iloc[0]))
                except Exception:
                    win_s = 60

            gg["window_idx"] = pd.to_numeric(gg["window_idx"], errors="coerce")
            gg["entropy_log2"] = pd.to_numeric(gg["entropy_log2"], errors="coerce")
            gg = gg.dropna(subset=["window_idx", "entropy_log2"]).sort_values(
                "window_idx", kind="mergesort"
            )
            if gg.empty:
                continue

            t_s = gg["window_idx"].to_numpy(dtype=np.float64) * float(win_s)
            y = gg["entropy_log2"].to_numpy(dtype=np.float64)

            fig, ax = plt.subplots(figsize=(8.4, 3.8))
            ax.plot(
                t_s,
                y,
                color=colors["adaptive"],
                marker="o",
                markersize=3.0,
                linewidth=1.6,
                label="entropy",
            )
            if smooth_w > 1 and len(y) >= smooth_w:
                y_s = (
                    pd.Series(y)
                    .rolling(window=smooth_w, min_periods=smooth_w)
                    .mean()
                    .to_numpy()
                )
                rm_label = f"rolling mean (w={smooth_w})"
                ax.plot(t_s, y_s, color="#111827", linewidth=2.0, label=rm_label)

            # max entropy guide (log2(K))
            if (
                not arm_distribution.empty
                and {"run_id", "profile", "sensor", "arm_id"}.issubset(arm_distribution.columns)
            ):
                mask = (
                    (arm_distribution["run_id"].astype("string") == str(run_id))
                    & (arm_distribution["profile"].astype("string") == str(prof))
                    & (arm_distribution["sensor"].astype("string") == str(sensor))
                )
                arm_ids = pd.to_numeric(arm_distribution.loc[mask, "arm_id"], errors="coerce")
                k = int(arm_ids.nunique())
                if k >= 2:
                    h_max = float(math.log2(k))
                    ax.axhline(h_max, color="#9CA3AF", linestyle="--", linewidth=1.0, alpha=0.8)
                    ax.text(
                        float(t_s[0]),
                        h_max,
                        f"  max log2(K)={h_max:.2f}",
                        va="bottom",
                        fontsize=9,
                        color="#6B7280",
                    )

            ax.set_title(f"Action entropy (window={win_s}s) · {sensor}/{prof} · run={run_id}")
            ax.set_xlabel("time since run start [s]")
            ax.set_ylabel("entropy [bits]")
            ax.grid(alpha=0.25)
            ax.set_axisbelow(True)
            if smooth_w > 1:
                ax.legend(loc="best", frameon=True)
            fig.tight_layout()
            created.extend(
                _save_figure_multi(
                    fig,
                    figs_dir,
                    base_name=_fig_basename(
                        sensor=str(sensor),
                        profile=str(prof),
                        policy="adaptive",
                        metric=f"entropy_{win_s}s",
                        run_id=str(run_id),
                    ),
                    cfg=plot_cfg,
                )
            )
            plt.close(fig)

    # ------------------- (B3) Safe-arm 강제 비율/원인 -------------------
    if not summary.empty and {"profile", "policy", "sensor"}.issubset(summary.columns):
        s = summary.copy()
        s = s[s["policy"].astype("string") == "adaptive"]
        need = {
            "linucb_safe_forced_rate",
            "linucb_forced_reason_aoi_limit_rate",
            "linucb_forced_reason_mae_limit_rate",
            "linucb_forced_reason_both_rate",
        }
        if need.issubset(s.columns):
            for sensor, g in s.groupby("sensor", sort=False, observed=True):
                gg = g.sort_values("profile", kind="mergesort").reset_index(drop=True)
                profiles = [str(p) for p in gg["profile"].tolist()]

                aoi = pd.to_numeric(
                    gg["linucb_forced_reason_aoi_limit_rate"], errors="coerce"
                ).fillna(0.0)
                mae = pd.to_numeric(
                    gg["linucb_forced_reason_mae_limit_rate"], errors="coerce"
                ).fillna(0.0)
                both = (
                    pd.to_numeric(gg["linucb_forced_reason_both_rate"], errors="coerce")
                    .fillna(0.0)
                )
                forced = pd.to_numeric(gg["linucb_safe_forced_rate"], errors="coerce")
                if not np.isfinite(forced.to_numpy(dtype=np.float64)).any():
                    continue

                forced_std = None
                if "linucb_safe_forced_rate_std" in gg.columns:
                    forced_std = pd.to_numeric(gg["linucb_safe_forced_rate_std"], errors="coerce")

                x = np.arange(len(profiles))
                fig, ax = plt.subplots(figsize=(8.8, 4.2))
                ax.bar(x, (aoi * 100.0).to_numpy(), label="AOI_LIMIT", color="#2563EB")
                ax.bar(
                    x,
                    (mae * 100.0).to_numpy(),
                    bottom=(aoi * 100.0).to_numpy(),
                    label="MAE_LIMIT",
                    color="#F59E0B",
                )
                ax.bar(
                    x,
                    (both * 100.0).to_numpy(),
                    bottom=((aoi + mae) * 100.0).to_numpy(),
                    label="BOTH",
                    color="#EF4444",
                )
                ax.set_xticks(x)
                ax.set_xticklabels(profiles, rotation=0)
                ax.set_xlabel(LABEL_LINK_PROFILE)
                ax.set_ylim(0, 100)
                ax.set_ylabel("Safe-arm forced rate [%]")
                ax.set_title(f"Safe-arm interventions (adaptive) · sensor={sensor}")
                ax.grid(axis="y", alpha=0.25)
                ax.set_axisbelow(True)
                ax.legend(loc="best", frameon=True)

                for i in range(len(profiles)):
                    v_pct = float(forced.iloc[i]) * 100.0 if i < len(forced) else float("nan")
                    if not math.isfinite(v_pct):
                        continue
                    label = f"{v_pct:.1f}%"
                    if forced_std is not None and i < len(forced_std):
                        std_v = float(forced_std.iloc[i])
                        if math.isfinite(std_v):
                            label = f"{v_pct:.1f}±{std_v * 100.0:.1f}%"
                    ax.text(i, min(99.0, v_pct + 1.5), label, ha="center", fontsize=9)

                fig.tight_layout()
                created.extend(
                    _save_figure_multi(
                        fig,
                        figs_dir,
                        base_name=_fig_basename(
                            sensor=str(sensor),
                            profile="all",
                            policy="adaptive",
                            metric="safe_forced_reasons",
                        ),
                        cfg=plot_cfg,
                    )
                )
                plt.close(fig)

    # ------------------- (B5) Policy switch rate -------------------
    need_switch = {"policy", "sensor", "profile", "linucb_switch_rate"}
    if not summary.empty and need_switch.issubset(summary.columns):
        s = summary.copy()
        s = s[s["policy"].astype("string") == "adaptive"]
        for sensor, g in s.groupby("sensor", sort=False, observed=True):
            gg = g.sort_values("profile", kind="mergesort").reset_index(drop=True)
            y = pd.to_numeric(gg["linucb_switch_rate"], errors="coerce")
            if not np.isfinite(y.to_numpy(dtype=np.float64)).any():
                continue
            yerr = pd.Series([0.0] * len(gg))
            if "linucb_switch_rate_std" in gg.columns:
                yerr = pd.to_numeric(gg["linucb_switch_rate_std"], errors="coerce").fillna(0.0)

            profiles = [str(p) for p in gg["profile"].tolist()]
            x = np.arange(len(profiles))
            fig, ax = plt.subplots(figsize=(8.8, 3.8))
            ax.bar(
                x,
                y.to_numpy(dtype=np.float64),
                yerr=yerr.to_numpy(dtype=np.float64),
                capsize=3,
                color=colors["adaptive"],
            )
            ax.set_xticks(x)
            ax.set_xticklabels(profiles, rotation=0)
            ax.set_xlabel(LABEL_LINK_PROFILE)
            ax.set_ylim(0, 1.0)
            ax.set_ylabel("Switch rate P[arm_t ≠ arm_{t-1}]")
            ax.set_title(f"Policy switching rate (adaptive) · sensor={sensor}")
            ax.grid(axis="y", alpha=0.25)
            ax.set_axisbelow(True)
            fig.tight_layout()
            created.extend(
                _save_figure_multi(
                    fig,
                    figs_dir,
                    base_name=_fig_basename(
                        sensor=str(sensor), profile="all", policy="adaptive", metric="switch_rate"
                    ),
                    cfg=plot_cfg,
                )
            )
            plt.close(fig)

    # ------------------- (B6) Rate-limit skips (조건부) -------------------
    skips_col = "linucb_rate_limit_skips_per_decision"
    if not summary.empty and {"policy", "sensor", "profile", skips_col}.issubset(summary.columns):
        s = summary.copy()
        s = s[s["policy"].astype("string") == "adaptive"]
        for sensor, g in s.groupby("sensor", sort=False, observed=True):
            gg = g.sort_values("profile", kind="mergesort").reset_index(drop=True)
            y = pd.to_numeric(gg[skips_col], errors="coerce").fillna(0.0)
            if float(y.max()) <= 0.0:
                continue
            yerr = pd.Series([0.0] * len(gg))
            std_col = f"{skips_col}_std"
            if std_col in gg.columns:
                yerr = pd.to_numeric(gg[std_col], errors="coerce").fillna(0.0)

            profiles = [str(p) for p in gg["profile"].tolist()]
            x = np.arange(len(profiles))
            fig, ax = plt.subplots(figsize=(8.8, 3.8))
            ax.bar(
                x,
                y.to_numpy(dtype=np.float64),
                yerr=yerr.to_numpy(dtype=np.float64),
                capsize=3,
                color="#6B7280",
            )
            ax.set_xticks(x)
            ax.set_xticklabels(profiles, rotation=0)
            ax.set_xlabel(LABEL_LINK_PROFILE)
            ax.set_ylabel("Rate-limit skips / decision [count]")
            ax.set_title(f"Rate-limit skips (adaptive) · sensor={sensor}")
            ax.grid(axis="y", alpha=0.25)
            ax.set_axisbelow(True)
            fig.tight_layout()
            created.extend(
                _save_figure_multi(
                    fig,
                    figs_dir,
                    base_name=_fig_basename(
                        sensor=str(sensor),
                        profile="all",
                        policy="adaptive",
                        metric="rate_limit_skips_per_decision",
                    ),
                    cfg=plot_cfg,
                )
            )
            plt.close(fig)

    # ------------------- (B4) UCB 분해(요약) -------------------
    if not summary.empty and {"profile", "policy", "sensor"}.issubset(summary.columns):
        s = summary.copy()
        s = s[s["policy"].astype("string") == "adaptive"]
        need = {
            "linucb_ucb_exploitation_mean",
            "linucb_ucb_exploration_mean",
            "linucb_ucb_score_mean",
            "linucb_ucb_uncertainty_mean",
        }
        if need.issubset(s.columns):
            for (prof, sensor), g in s.groupby(["profile", "sensor"], sort=False, observed=True):
                r = g.iloc[0]
                expl = float(pd.to_numeric(r.get("linucb_ucb_exploitation_mean"), errors="coerce"))
                expo = float(pd.to_numeric(r.get("linucb_ucb_exploration_mean"), errors="coerce"))
                score = float(pd.to_numeric(r.get("linucb_ucb_score_mean"), errors="coerce"))
                u_val = float(pd.to_numeric(r.get("linucb_ucb_uncertainty_mean"), errors="coerce"))
                if not (
                    math.isfinite(expl)
                    or math.isfinite(expo)
                    or math.isfinite(score)
                    or math.isfinite(u_val)
                ):
                    continue

                fig, (ax1, ax2) = plt.subplots(nrows=2, ncols=1, figsize=(7.6, 5.6))
                labels = ["exploitation (θ·x)", "exploration (α·u)", "score"]
                x = np.arange(len(labels))
                ax1.bar(
                    x,
                    [expl, expo, score],
                    color=["#111827", "#F59E0B", "#2563EB"],
                )
                ax1.set_xticks(x)
                ax1.set_xticklabels(labels, rotation=0)
                ax1.set_xlabel(LABEL_UCB_TERM)
                ax1.set_ylabel("UCB terms [reward units]")
                ax1.grid(axis="y", alpha=0.25)
                ax1.set_axisbelow(True)

                ax2.bar([0], [u_val], color="#6B7280")
                ax2.set_xticks([0])
                ax2.set_xticklabels(["uncertainty u"])
                ax2.set_xlabel(LABEL_UCB_TERM)
                ax2.set_ylabel("u [a.u.]")
                ax2.grid(axis="y", alpha=0.25)
                ax2.set_axisbelow(True)

                fig.suptitle(f"UCB decomposition (mean) · {sensor}/{prof}", y=1.02)
                fig.tight_layout()
                created.extend(
                    _save_figure_multi(
                        fig,
                        figs_dir,
                        base_name=_fig_basename(
                            sensor=str(sensor),
                            profile=str(prof),
                            policy="adaptive",
                            metric="ucb_decomposition",
                        ),
                        cfg=plot_cfg,
                    )
                )
                plt.close(fig)

    # ------------------- (B7) Event reason breakdown -------------------
    # THRESHOLD/HEARTBEAT는 events 기준, RATE_LIMIT_SKIP는 decisions의 누적 스킵 수 기준(참고용).
    need_cols = {
        "profile",
        "policy",
        "sensor",
        "event_reason_threshold_count",
        "event_reason_heartbeat_count",
        "linucb_rate_limit_skips_total",
    }
    if not by_run.empty and need_cols.issubset(by_run.columns):
        br = by_run.copy()
        br = br[br["policy"].astype("string") == "adaptive"]
        for sensor, g in br.groupby("sensor", sort=False, observed=True):
            rows: list[dict[str, object]] = []
            for prof, gp in g.groupby("profile", sort=False, observed=True):
                thr = pd.to_numeric(gp["event_reason_threshold_count"], errors="coerce")
                hb = pd.to_numeric(gp["event_reason_heartbeat_count"], errors="coerce")
                sk = pd.to_numeric(gp["linucb_rate_limit_skips_total"], errors="coerce")

                if not (thr.notna().any() or hb.notna().any() or sk.notna().any()):
                    continue

                total = (thr + hb + sk).replace([np.inf, -np.inf], np.nan)
                frac_thr = (thr / total).replace([np.inf, -np.inf], np.nan)
                frac_hb = (hb / total).replace([np.inf, -np.inf], np.nan)
                frac_sk = (sk / total).replace([np.inf, -np.inf], np.nan)

                rows.append(
                    {
                        "profile": str(prof),
                        "thr_pct": float(frac_thr.mean(skipna=True) * 100.0)
                        if frac_thr.notna().any()
                        else float("nan"),
                        "hb_pct": float(frac_hb.mean(skipna=True) * 100.0)
                        if frac_hb.notna().any()
                        else float("nan"),
                        "sk_pct": float(frac_sk.mean(skipna=True) * 100.0)
                        if frac_sk.notna().any()
                        else float("nan"),
                        "total_mean": float(total.mean(skipna=True))
                        if total.notna().any()
                        else float("nan"),
                    }
                )

            if not rows:
                continue
            dfp = pd.DataFrame(rows).sort_values("profile", kind="mergesort").reset_index(drop=True)

            thr_y = (
                pd.to_numeric(dfp["thr_pct"], errors="coerce")
                .fillna(0.0)
                .to_numpy(dtype=np.float64)
            )
            hb_y = (
                pd.to_numeric(dfp["hb_pct"], errors="coerce")
                .fillna(0.0)
                .to_numpy(dtype=np.float64)
            )
            sk_y = (
                pd.to_numeric(dfp["sk_pct"], errors="coerce")
                .fillna(0.0)
                .to_numpy(dtype=np.float64)
            )
            if float(np.nanmax(thr_y + hb_y + sk_y)) <= 0.0:
                continue

            x = np.arange(len(dfp))
            fig, ax = plt.subplots(figsize=(8.8, 4.0))
            ax.bar(x, thr_y, label="THRESHOLD", color="#7C3AED")
            ax.bar(x, hb_y, bottom=thr_y, label="HEARTBEAT", color="#10B981")
            ax.bar(x, sk_y, bottom=thr_y + hb_y, label="RATE_LIMIT_SKIP", color="#9CA3AF")
            ax.set_xticks(x)
            ax.set_xticklabels(dfp["profile"].astype("string").tolist(), rotation=0)
            ax.set_xlabel(LABEL_LINK_PROFILE)
            ax.set_ylim(0, 100)
            ax.set_ylabel("Reason fraction [%]  (events + skips)")
            ax.set_title(f"Event reasons (adaptive) · sensor={sensor}")
            ax.grid(axis="y", alpha=0.25)
            ax.set_axisbelow(True)
            ax.legend(loc="best", frameon=True)
            fig.tight_layout()
            created.extend(
                _save_figure_multi(
                    fig,
                    figs_dir,
                    base_name=_fig_basename(
                        sensor=str(sensor),
                        profile="all",
                        policy="adaptive",
                        metric="event_reasons",
                    ),
                    cfg=plot_cfg,
                )
            )
            plt.close(fig)

    # ------------------- (C8) Outbox backlog time-series -------------------
    need_outbox = {"run_id", "profile", "policy", "state_q_len", "ts"}
    if not decisions_enriched.empty and need_outbox.issubset(decisions_enriched.columns):
        d = decisions_enriched.copy()
        d = d[d["policy"].astype("string") == "adaptive"]
        tcol = "t_recv_ns" if "t_recv_ns" in d.columns and d["t_recv_ns"].notna().any() else "ts"
        for (run_id, prof), g in d.groupby(["run_id", "profile"], sort=False, observed=True):
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
            ax.set_title(f"Outbox pending (decision-time samples) · profile={prof} · run={run_id}")
            ax.set_xlabel("time since run start [s]")
            ax.set_ylabel("pending() [count]")
            ax.grid(alpha=0.25)
            ax.set_axisbelow(True)
            fig.tight_layout()
            created.extend(
                _save_figure_multi(
                    fig,
                    figs_dir,
                    base_name=_fig_basename(
                        sensor="all",
                        profile=str(prof),
                        policy="adaptive",
                        metric="outbox_pending_ts",
                        run_id=str(run_id),
                    ),
                    cfg=plot_cfg,
                )
            )
            plt.close(fig)

    # ------------------- (C9) Duplicate bytes ratio -------------------
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
            ax.set_title(f"Duplicate bytes ratio (QoS1 de-dup) · profile={prof}")
            ax.grid(axis="y", alpha=0.25)
            ax.set_axisbelow(True)
            fig.tight_layout()
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

    # ------------------- (C10) E2E latency (boxplot) -------------------
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
                ax.set_ylabel("E2E latency (rx - gen) [ms]")
                ax.set_title(f"E2E latency distribution · {sensor}/{prof}")
                ax.grid(axis="y", alpha=0.25)
                ax.set_axisbelow(True)
                fig.tight_layout()
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

    # ------------------- (B4-optional) UCB time-series -------------------
    if bool(ucb_timeseries):
        need_ucb = {
            "run_id",
            "profile",
            "sensor",
            "policy",
            "ucb_exploitation",
            "ucb_exploration",
            "ucb_score",
            "ucb_alpha",
        }
        if not decisions_enriched.empty and need_ucb.issubset(decisions_enriched.columns):
            d = decisions_enriched.copy()
            d = d[d["policy"].astype("string") == "adaptive"]
            tcol = (
                "t_recv_ns"
                if "t_recv_ns" in d.columns and d["t_recv_ns"].notna().any()
                else "ts"
            )
            for (run_id, prof, sensor), g in d.groupby(
                ["run_id", "profile", "sensor"], sort=False, observed=True
            ):
                gg = g.copy()
                gg[tcol] = pd.to_numeric(gg[tcol], errors="coerce")
                ucb_cols = ["ucb_exploitation", "ucb_exploration", "ucb_score", "ucb_alpha"]
                for c in ucb_cols:
                    gg[c] = pd.to_numeric(gg[c], errors="coerce")
                gg = gg.dropna(subset=[tcol, *ucb_cols])
                gg = gg.sort_values(tcol, kind="mergesort")
                if gg.empty:
                    continue

                t0 = float(gg[tcol].iloc[0])
                t_s = (gg[tcol].to_numpy(dtype=np.float64) - t0) / 1e9
                expl = gg["ucb_exploitation"].to_numpy(dtype=np.float64)
                expo = gg["ucb_exploration"].to_numpy(dtype=np.float64)
                score = gg["ucb_score"].to_numpy(dtype=np.float64)
                alpha = gg["ucb_alpha"].to_numpy(dtype=np.float64)
                u = np.where(alpha > 0.0, expo / alpha, np.nan)

                fig, (ax1, ax2) = plt.subplots(nrows=2, ncols=1, figsize=(8.8, 5.2), sharex=True)
                ax1.plot(t_s, expl, label="exploitation (θ·x)", color="#111827")
                ax1.plot(t_s, expo, label="exploration (α·u)", color="#F59E0B")
                ax1.plot(t_s, score, label="score", color="#2563EB", alpha=0.9)
                ax1.set_ylabel("UCB terms [reward units]")
                ax1.grid(alpha=0.25)
                ax1.set_axisbelow(True)
                ax1.legend(loc="best", frameon=True)

                ax2.plot(t_s, u, label="uncertainty u", color="#6B7280")
                ax2.set_xlabel("time since run start [s]")
                ax2.set_ylabel("u [a.u.]")
                ax2.grid(alpha=0.25)
                ax2.set_axisbelow(True)
                ax2.legend(loc="best", frameon=True)

                fig.suptitle(f"UCB terms over time · {sensor}/{prof} · run={run_id}", y=1.02)
                fig.tight_layout()
                created.extend(
                    _save_figure_multi(
                        fig,
                        figs_dir,
                        base_name=_fig_basename(
                            sensor=str(sensor),
                            profile=str(prof),
                            policy="adaptive",
                            metric="ucb_terms_ts",
                            run_id=str(run_id),
                        ),
                        cfg=plot_cfg,
                    )
                )
                plt.close(fig)

    return created



__all__ = [
    "_try_make_diagnostic_plots",
    "_try_make_paper_plots",
    "_try_make_pipeline_plots",
    "_try_make_plots",
]
