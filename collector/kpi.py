"""KPI aggregation and strict final-evaluation helpers.

This module is intentionally small and dependency-light so KPI logic can be
tested independently from the large analyzer CLI/reporting surface.
"""

from __future__ import annotations

import math

import pandas as pd

from common.metrics import percent_improvement

_POLICY_ORDER = ["periodic", "fixed_tau", "adaptive"]


def summarize(summary_by_run: pd.DataFrame) -> pd.DataFrame:
    """Summarize per-run metrics into profile/policy/sensor aggregates."""
    need = {
        "run_id",
        "profile",
        "policy",
        "sensor",
        "n_events",
        "duration_s",
        "rate_Bps",
        "aoi_mean_ms",
        "aoi_p95_ms",
        "mae_event_mean",
        "mae_event_p95",
        "kbits_mean",
    }
    if not need.issubset(summary_by_run.columns):
        missing = sorted(need - set(summary_by_run.columns))
        raise ValueError(f"missing columns for summarize: {missing}")

    metric_cols = [
        "duration_s",
        "rate_Bps",
        "aoi_mean_ms",
        "aoi_p95_ms",
        "mae_event_mean",
        "mae_event_p95",
        "kbits_mean",
    ]
    for c in [
        "event_rate_hz",
        "send_ratio",
        "rx_delay_mean_ms",
        "rx_delay_p50_ms",
        "rx_delay_p95_ms",
        "action_unique_count",
        "action_switch_rate",
        "event_reason_threshold_frac",
        "event_reason_heartbeat_frac",
        "dup_bytes_ratio",
        "linucb_n_decisions",
        "linucb_action_entropy_mean_60s",
        "linucb_switch_rate",
        "linucb_safe_forced_rate",
        "linucb_forced_reason_none_rate",
        "linucb_forced_reason_aoi_limit_rate",
        "linucb_forced_reason_mae_limit_rate",
        "linucb_forced_reason_both_rate",
        "linucb_ucb_exploitation_mean",
        "linucb_ucb_exploration_mean",
        "linucb_ucb_score_mean",
        "linucb_ucb_uncertainty_mean",
        "linucb_reward_mean",
        "linucb_reward_aoi_mean",
        "linucb_reward_mae_mean",
        "linucb_reward_rate_mean",
        "linucb_rate_limit_skips_total",
        "linucb_rate_limit_skips_per_decision",
        "outbox_pending_max",
        "outbox_pending_auc_s",
        "outbox_pending_recovery_s",
        "recon_mae_mean",
        "recon_mae_p95",
        "recon_mae_p99",
        "recon_mae_max",
        "anomaly_tau_ref",
        "anomaly_segments",
        "anomaly_segments_hit",
        "anomaly_segment_recall",
    ]:
        if c in summary_by_run.columns:
            metric_cols.append(c)

    rows = []
    for (prof, pol, sensor), g in summary_by_run.groupby(
        ["profile", "policy", "sensor"], sort=False, observed=True
    ):
        n_runs = int(g["run_id"].nunique())
        row = {
            "profile": str(prof),
            "policy": str(pol),
            "sensor": str(sensor),
            "n_runs": n_runs,
            "n_events": int(g["n_events"].sum()),
        }
        for c in ["n_samples_est", "n_suppressed_est"]:
            if c in g.columns:
                row[c] = int(g[c].sum())
        for c in metric_cols:
            row[c] = float(g[c].mean())
            row[f"{c}_std"] = float(g[c].std(ddof=1)) if n_runs >= 2 else float("nan")
        rows.append(row)

    out = pd.DataFrame(rows)
    out["policy"] = pd.Categorical(out["policy"], categories=_POLICY_ORDER, ordered=True)
    out = out.sort_values(["profile", "policy", "sensor"]).reset_index(drop=True)
    return out


def compare_policies(
    summary: pd.DataFrame,
    *,
    baseline_policy: str = "periodic",
) -> pd.DataFrame:
    """Compute policy deltas against a baseline policy."""
    if summary.empty:
        return pd.DataFrame()

    required = {
        "profile",
        "policy",
        "sensor",
        "rate_Bps",
        "aoi_mean_ms",
        "aoi_p95_ms",
        "mae_event_mean",
        "mae_event_p95",
    }
    if not required.issubset(summary.columns):
        missing = sorted(required - set(summary.columns))
        raise ValueError(f"missing columns for compare_policies: {missing}")

    base = summary[summary["policy"] == baseline_policy].copy()
    base_keyed = base.set_index(["profile", "sensor"], drop=False)

    rows = []
    for _, r in summary.iterrows():
        key = (r["profile"], r["sensor"])
        b = base_keyed.loc[key] if key in base_keyed.index else None

        def _base(col: str) -> float:
            if b is None:
                return float("nan")
            return float(b[col])

        row = {
            "profile": str(r["profile"]),
            "sensor": str(r["sensor"]),
            "policy": str(r["policy"]),
            "baseline_policy": str(baseline_policy),
            "baseline_rate_Bps": _base("rate_Bps"),
            "baseline_aoi_mean_ms": _base("aoi_mean_ms"),
            "baseline_aoi_p95_ms": _base("aoi_p95_ms"),
            "baseline_mae_event_mean": _base("mae_event_mean"),
            "baseline_mae_event_p95": _base("mae_event_p95"),
        }
        for c in ["recon_mae_mean", "recon_mae_p95", "recon_mae_p99", "recon_mae_max"]:
            if c in summary.columns:
                row[f"baseline_{c}"] = _base(c)

        metric_pairs: list[tuple[str, str]] = [
            ("rate_Bps", "Bps"),
            ("aoi_mean_ms", "ms"),
            ("aoi_p95_ms", "ms"),
            ("mae_event_mean", "mae"),
            ("mae_event_p95", "mae"),
        ]
        for c in ["recon_mae_mean", "recon_mae_p95", "recon_mae_p99", "recon_mae_max"]:
            if c in summary.columns:
                metric_pairs.append((c, "mae"))

        for col, unit in metric_pairs:
            cand = float(r[col])
            basev = float(row[f"baseline_{col}"])
            row[f"{col}_delta_{unit}"] = cand - basev
            row[f"{col}_improvement_pct"] = percent_improvement(basev, cand)

        rows.append(row)

    out = pd.DataFrame(rows)
    out["policy"] = pd.Categorical(out["policy"], categories=_POLICY_ORDER, ordered=True)
    out = out.sort_values(["profile", "sensor", "policy"]).reset_index(drop=True)
    return out


def _keyed_rows(df: pd.DataFrame) -> dict[tuple[str, str, str], pd.Series]:
    if df.empty:
        return {}
    return {
        (str(r["profile"]), str(r["sensor"]), str(r["policy"])): r for _, r in df.iterrows()
    }


def compute_final_kpi(summary: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """Compute strict final KPI verdicts (PASS/FAIL) for adaptive policy."""
    if summary.empty:
        return pd.DataFrame(), False

    adaptive = summary[summary["policy"].astype("string") == "adaptive"]
    if adaptive.empty:
        return pd.DataFrame(), False

    comp_periodic = compare_policies(summary, baseline_policy="periodic")
    comp_fixed = compare_policies(summary, baseline_policy="fixed_tau")

    comp_periodic_k = _keyed_rows(comp_periodic)
    comp_fixed_k = _keyed_rows(comp_fixed)
    summary_k = _keyed_rows(summary)

    pairs: list[tuple[str, str]] = [
        (str(r["profile"]), str(r["sensor"]))
        for _, r in adaptive[["profile", "sensor"]].drop_duplicates().iterrows()
    ]

    rows: list[dict[str, object]] = []
    for prof, sensor in pairs:
        pol = "adaptive"
        key = (prof, sensor, pol)

        r_p = comp_periodic_k.get(key)
        r_f = comp_fixed_k.get(key)
        r_s = summary_k.get(key)

        rate_imp_periodic = (
            float(r_p.get("rate_Bps_improvement_pct")) if r_p is not None else float("nan")
        )
        rate_imp_fixed = (
            float(r_f.get("rate_Bps_improvement_pct")) if r_f is not None else float("nan")
        )

        # Guard against the undefined-percent branch when fixed baseline is ~0.
        recon_p95_imp_fixed = float("nan")
        fixed_row = summary_k.get((prof, sensor, "fixed_tau"))
        if fixed_row is not None and r_s is not None:
            basev = float(fixed_row.get("recon_mae_p95", float("nan")))
            cand = float(r_s.get("recon_mae_p95", float("nan")))
            if math.isfinite(basev) and math.isfinite(cand):
                eps = 1e-12
                if basev <= eps:
                    recon_p95_imp_fixed = 0.0 if cand <= eps else -100.0
                else:
                    recon_p95_imp_fixed = percent_improvement(basev, cand)

        aoi_p95_imp_fixed = (
            float(r_f.get("aoi_p95_ms_improvement_pct")) if r_f is not None else float("nan")
        )
        anomaly_recall = (
            float(r_s.get("anomaly_segment_recall", float("nan")))
            if r_s is not None
            else float("nan")
        )

        k1 = bool(math.isfinite(rate_imp_periodic) and rate_imp_periodic >= 85.0)
        k2 = bool(math.isfinite(rate_imp_fixed) and rate_imp_fixed >= -10.0)
        k3 = bool(math.isfinite(recon_p95_imp_fixed) and recon_p95_imp_fixed >= -10.0)
        k4 = bool(math.isfinite(anomaly_recall) and anomaly_recall >= 0.90)
        k5 = bool(math.isfinite(aoi_p95_imp_fixed) and aoi_p95_imp_fixed >= -10.0)
        overall = bool(k1 and k2 and k3 and k4 and k5)

        rows.append(
            {
                "profile": prof,
                "sensor": sensor,
                "policy": pol,
                "rate_improvement_vs_periodic_pct": float(rate_imp_periodic),
                "rate_improvement_vs_fixed_tau_pct": float(rate_imp_fixed),
                "recon_mae_p95_improvement_vs_fixed_tau_pct": float(recon_p95_imp_fixed),
                "anomaly_segment_recall": float(anomaly_recall),
                "aoi_p95_improvement_vs_fixed_tau_pct": float(aoi_p95_imp_fixed),
                "kpi1_rate_vs_periodic": "PASS" if k1 else "FAIL",
                "kpi2_rate_vs_fixed_tau": "PASS" if k2 else "FAIL",
                "kpi3_recon_p95_vs_fixed_tau": "PASS" if k3 else "FAIL",
                "kpi4_anomaly_segment_recall": "PASS" if k4 else "FAIL",
                "kpi5_aoi_p95_vs_fixed_tau": "PASS" if k5 else "FAIL",
                "overall": "PASS" if overall else "FAIL",
            }
        )

    out = pd.DataFrame(rows)
    project_pass = bool((not out.empty) and (out["overall"] == "PASS").all())
    return out, project_pass
