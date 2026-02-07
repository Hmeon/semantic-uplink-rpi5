"""Paper/report-focused plot generators."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from collector.load_normalize import enrich_decisions_with_events
from collector.plot_config import PlotConfig
from collector.plot_generator_utils import (
    _apply_plot_style,
    _fig_basename,
    _maybe_import_matplotlib,
    _save_figure_multi,
)
from collector.plot_labels import LABEL_LINK_PROFILE
from common.config import load_policy_config_dict
from common.schema import LinkProfile


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



__all__ = ["_try_make_paper_plots"]

