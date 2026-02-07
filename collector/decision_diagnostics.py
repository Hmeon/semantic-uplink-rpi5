"""LinUCB decision diagnostics aggregations extracted from collector.analyze."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _entropy_log2_from_counts(counts: np.ndarray) -> float:
    total = float(np.sum(counts))
    if total <= 0:
        return float("nan")
    p = np.asarray(counts, dtype=np.float64) / total
    p = p[p > 0]
    if p.size == 0:
        return float("nan")
    return float(-(p * np.log2(p)).sum())


def summarize_decisions_diagnostics_by_run(
    decisions: pd.DataFrame,
    *,
    window_s: int = 60,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Summarize LinUCB diagnostics from decision logs."""
    if decisions.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    d = decisions.copy()
    for col in ["run_id", "profile", "policy", "sensor"]:
        if col not in d.columns:
            d[col] = "unknown"
        d[col] = d[col].astype("string")

    time_col = "ts"
    if "t_recv_ns" in d.columns and d["t_recv_ns"].notna().any():
        time_col = "t_recv_ns"
        d["_t_ns"] = d["t_recv_ns"].fillna(d["ts"]).astype("int64")
    else:
        d["_t_ns"] = d["ts"].astype("int64")

    window_ns = int(max(1, int(window_s)) * 1_000_000_000)
    entropy_col = f"linucb_action_entropy_mean_{int(window_s)}s"
    keys = ["run_id", "profile", "policy", "sensor"]

    diag_rows: list[dict[str, object]] = []
    arm_rows: list[dict[str, object]] = []
    entropy_rows: list[dict[str, object]] = []

    for (run_id, prof, pol, sensor), g in d.groupby(keys, sort=False, observed=True):
        g = g.sort_values("_t_ns", kind="mergesort").reset_index(drop=True)
        row: dict[str, object] = {
            "run_id": str(run_id),
            "profile": str(prof),
            "policy": str(pol),
            "sensor": str(sensor),
            "linucb_n_decisions": int(len(g)),
        }

        outbox_max = float("nan")
        outbox_auc_s = float("nan")
        outbox_recovery_s = float("nan")
        if "state_q_len" in g.columns and "ts" in g.columns and len(g) > 0:
            t = g["ts"].astype("int64").to_numpy()
            q = g["state_q_len"].astype("float64").to_numpy()
            order = np.argsort(t, kind="mergesort")
            t = t[order]
            q = q[order]
            if q.size > 0 and np.isfinite(q).any():
                outbox_max = float(np.nanmax(q))
            if q.size >= 2:
                dt_s = np.diff(t.astype("int64")) / 1e9
                avg_q = (q[:-1] + q[1:]) / 2.0
                outbox_auc_s = float(np.nansum(avg_q * dt_s))
            if q.size > 0 and np.isfinite(outbox_max):
                i_max = int(np.nanargmax(q))
                after_zero = np.where(q[i_max:] == 0)[0]
                if after_zero.size > 0:
                    j = i_max + int(after_zero[0])
                    outbox_recovery_s = float((t[j] - t[i_max]) / 1e9)
        row["outbox_pending_max"] = float(outbox_max)
        row["outbox_pending_auc_s"] = float(outbox_auc_s)
        row["outbox_pending_recovery_s"] = float(outbox_recovery_s)

        row["linucb_switch_rate"] = float("nan")
        row[entropy_col] = float("nan")
        ga = pd.DataFrame()
        if "arm_id" in g.columns and g["arm_id"].notna().any():
            ga = g.dropna(subset=["arm_id"]).copy()
            ga["arm_id"] = ga["arm_id"].astype("int64")
        elif (
            "tau" in g.columns
            and "kbits" in g.columns
            and g["tau"].notna().any()
            and g["kbits"].notna().any()
        ):
            ga = g.dropna(subset=["tau", "kbits"]).copy()
            ga["tau"] = pd.to_numeric(ga["tau"], errors="coerce")
            ga["kbits"] = pd.to_numeric(ga["kbits"], errors="coerce")
            ga = ga.replace([np.inf, -np.inf], np.nan).dropna(subset=["tau", "kbits"])
            if not ga.empty:
                uniq = (
                    ga[["tau", "kbits"]]
                    .drop_duplicates()
                    .sort_values(["tau", "kbits"], kind="mergesort")
                    .reset_index(drop=True)
                )
                uniq["_arm_id"] = np.arange(int(len(uniq)), dtype=np.int64)
                ga = ga.merge(uniq, how="left", on=["tau", "kbits"])
                ga["arm_id"] = ga["_arm_id"].astype("int64")

        if not ga.empty:
            counts = ga["arm_id"].value_counts().sort_index()
            total = int(counts.sum())
            for arm_id, cnt in counts.items():
                tau_v = float("nan")
                kbits_v: int | None = None
                try:
                    sub = ga.loc[ga["arm_id"] == int(arm_id)]
                    if not sub.empty:
                        if "tau" in sub.columns and sub["tau"].notna().any():
                            tau_v = float(sub["tau"].iloc[0])
                        if "kbits" in sub.columns and sub["kbits"].notna().any():
                            kbits_v = int(sub["kbits"].iloc[0])
                except Exception:
                    tau_v = float("nan")
                    kbits_v = None
                arm_rows.append(
                    {
                        "run_id": str(run_id),
                        "profile": str(prof),
                        "policy": str(pol),
                        "sensor": str(sensor),
                        "arm_id": int(arm_id),
                        "tau": float(tau_v),
                        "kbits": int(kbits_v) if kbits_v is not None else pd.NA,
                        "count": int(cnt),
                        "frac": float(cnt / total) if total > 0 else float("nan"),
                        "n_decisions": int(total),
                    }
                )

            av = ga["arm_id"].to_numpy()
            if av.size >= 2:
                row["linucb_switch_rate"] = float(np.mean(av[1:] != av[:-1]))

            t0 = int(ga["_t_ns"].iloc[0])
            ga["_window_idx"] = ((ga["_t_ns"] - t0) // window_ns).astype("int64")
            win_entropies = []
            for w, gw in ga.groupby("_window_idx", sort=False, observed=True):
                c = gw["arm_id"].value_counts().to_numpy(dtype=np.float64)
                h = _entropy_log2_from_counts(c)
                entropy_rows.append(
                    {
                        "run_id": str(run_id),
                        "profile": str(prof),
                        "policy": str(pol),
                        "sensor": str(sensor),
                        "time_base": str(time_col),
                        "window_s": int(window_s),
                        "window_idx": int(w),
                        "n_decisions": int(len(gw)),
                        "entropy_log2": float(h),
                    }
                )
                if math.isfinite(h):
                    win_entropies.append(float(h))
            if win_entropies:
                row[entropy_col] = float(np.mean(win_entropies))

        row["linucb_safe_forced_rate"] = float("nan")
        if "safe_arm_forced" in g.columns and g["safe_arm_forced"].notna().any():
            s = g["safe_arm_forced"].astype("boolean")
            row["linucb_safe_forced_rate"] = float(s.mean(skipna=True))

        for name in [
            "linucb_forced_reason_none_rate",
            "linucb_forced_reason_aoi_limit_rate",
            "linucb_forced_reason_mae_limit_rate",
            "linucb_forced_reason_both_rate",
        ]:
            row[name] = float("nan")
        if "forced_reason" in g.columns and g["forced_reason"].notna().any():
            fr = g["forced_reason"].astype("string").fillna("")
            if len(fr) > 0:
                row["linucb_forced_reason_none_rate"] = float((fr == "NONE").mean())
                row["linucb_forced_reason_aoi_limit_rate"] = float((fr == "AOI_LIMIT").mean())
                row["linucb_forced_reason_mae_limit_rate"] = float((fr == "MAE_LIMIT").mean())
                row["linucb_forced_reason_both_rate"] = float((fr == "BOTH").mean())

        for src, dst in [
            ("ucb_exploitation", "linucb_ucb_exploitation_mean"),
            ("ucb_exploration", "linucb_ucb_exploration_mean"),
            ("ucb_score", "linucb_ucb_score_mean"),
        ]:
            row[dst] = float("nan")
            if src in g.columns and g[src].notna().any():
                row[dst] = float(pd.to_numeric(g[src], errors="coerce").mean())

        row["linucb_ucb_uncertainty_mean"] = float("nan")
        if (
            "ucb_exploration" in g.columns
            and "ucb_alpha" in g.columns
            and g["ucb_exploration"].notna().any()
            and g["ucb_alpha"].notna().any()
        ):
            exploration = pd.to_numeric(g["ucb_exploration"], errors="coerce")
            alpha = pd.to_numeric(g["ucb_alpha"], errors="coerce")
            u = (exploration / alpha).replace([np.inf, -np.inf], np.nan)
            if u.notna().any():
                row["linucb_ucb_uncertainty_mean"] = float(u.mean())

        row["linucb_reward_mean"] = float("nan")
        if "reward" in g.columns and g["reward"].notna().any():
            row["linucb_reward_mean"] = float(pd.to_numeric(g["reward"], errors="coerce").mean())

        for src, dst in [
            ("reward_aoi", "linucb_reward_aoi_mean"),
            ("reward_mae", "linucb_reward_mae_mean"),
            ("reward_rate", "linucb_reward_rate_mean"),
        ]:
            row[dst] = float("nan")
            if src in g.columns and g[src].notna().any():
                row[dst] = float(pd.to_numeric(g[src], errors="coerce").mean())

        row["linucb_rate_limit_skips_total"] = float("nan")
        row["linucb_rate_limit_skips_per_decision"] = float("nan")
        if "rate_limit_skips" in g.columns and g["rate_limit_skips"].notna().any():
            skips = pd.to_numeric(g["rate_limit_skips"], errors="coerce").fillna(0.0)
            total_skips = float(skips.sum())
            row["linucb_rate_limit_skips_total"] = float(total_skips)
            if len(g) > 0:
                row["linucb_rate_limit_skips_per_decision"] = float(total_skips / len(g))

        diag_rows.append(row)

    diag = pd.DataFrame(diag_rows)
    arm_dist = pd.DataFrame(arm_rows)
    entropy = pd.DataFrame(entropy_rows)
    for c in ["_t_ns"]:
        if c in diag.columns:
            diag.drop(columns=[c], inplace=True)
        if c in arm_dist.columns:
            arm_dist.drop(columns=[c], inplace=True)
        if c in entropy.columns:
            entropy.drop(columns=[c], inplace=True)
    return diag, arm_dist, entropy


__all__ = ["summarize_decisions_diagnostics_by_run"]
