"""Diagnostic plot generators."""

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
    LABEL_ARM,
    LABEL_DUP_BYTES_RATIO_PCT,
    LABEL_LINK_PROFILE,
    LABEL_POLICY,
    LABEL_UCB_TERM,
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




__all__ = ["_try_make_diagnostic_plots"]

