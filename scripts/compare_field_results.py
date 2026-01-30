from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_float(x: object, *, ndigits: int) -> str:
    try:
        v = float(x)
    except Exception:
        return ""
    if pd.isna(v):
        return ""
    return f"{v:.{ndigits}f}"


def _md_table(df: pd.DataFrame) -> str:
    cols = [str(c) for c in df.columns]
    header = "| " + " | ".join(cols) + " |"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |"
    rows: list[str] = [header, sep]
    for row in df.itertuples(index=False):
        items = []
        for v in row:
            if pd.isna(v):
                items.append("")
            else:
                items.append(str(v))
        rows.append("| " + " | ".join(items) + " |")
    return "\n".join(rows)


def _select_metrics(df: pd.DataFrame) -> pd.DataFrame:
    keep = [
        "profile",
        "sensor",
        "policy",
        "n_events",
        "duration_s",
        "rate_Bps",
        "aoi_p95_ms",
        "mae_event_p95",
        "recon_mae_p95",
        "anomaly_segment_recall",
    ]
    cols = [c for c in keep if c in df.columns]
    out = df.loc[:, cols].copy()
    for c in ["n_events"]:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0).astype("int64")
    for c, nd in [
        ("duration_s", 1),
        ("rate_Bps", 3),
        ("aoi_p95_ms", 1),
        ("mae_event_p95", 6),
        ("recon_mae_p95", 6),
        ("anomaly_segment_recall", 3),
    ]:
        if c in out.columns:
            out[c] = out[c].map(lambda x, ndigits=nd: _fmt_float(x, ndigits=ndigits))
    return out


def _load_results(results_dir: Path) -> dict:
    metrics_path = results_dir / "metrics_summary.csv"
    verdict_path = results_dir / "kpi_verdict.json"
    if not metrics_path.exists():
        raise FileNotFoundError(f"missing {metrics_path}")
    if not verdict_path.exists():
        raise FileNotFoundError(f"missing {verdict_path}")

    metrics = pd.read_csv(metrics_path)
    verdict = _load_json(verdict_path)
    kpi_final_path = results_dir / "kpi_final.csv"
    kpi_final = pd.read_csv(kpi_final_path) if kpi_final_path.exists() else pd.DataFrame()
    return {"metrics": metrics, "verdict": verdict, "kpi_final": kpi_final}


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Compare Field A/B analysis results produced by collector.analyze"
    )
    ap.add_argument("--results-a", required=True, help="Scenario A results dir (has metrics_summary.csv)")
    ap.add_argument("--results-b", required=True, help="Scenario B results dir (has metrics_summary.csv)")
    ap.add_argument("--label-a", default="A", help="Label for scenario A (default: A)")
    ap.add_argument("--label-b", default="B", help="Label for scenario B (default: B)")
    ap.add_argument("--out", default="", help="Output directory (default: results/field_runs/compare_*)")
    ap.add_argument("--profile", default="", help="Optional profile filter (e.g., slow_10kbps)")
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    a_dir = Path(args.results_a)
    b_dir = Path(args.results_b)
    if not a_dir.is_dir():
        raise FileNotFoundError(f"--results-a is not a directory: {a_dir}")
    if not b_dir.is_dir():
        raise FileNotFoundError(f"--results-b is not a directory: {b_dir}")

    a = _load_results(a_dir)
    b = _load_results(b_dir)

    a_metrics = a["metrics"]
    b_metrics = b["metrics"]
    prof = str(args.profile).strip()
    if prof:
        if "profile" in a_metrics.columns:
            a_metrics = a_metrics[a_metrics["profile"].astype("string") == prof]
        if "profile" in b_metrics.columns:
            b_metrics = b_metrics[b_metrics["profile"].astype("string") == prof]

    a_sel = _select_metrics(a_metrics).sort_values(["profile", "sensor", "policy"], kind="mergesort")
    b_sel = _select_metrics(b_metrics).sort_values(["profile", "sensor", "policy"], kind="mergesort")

    key = [c for c in ["profile", "sensor", "policy"] if c in a_sel.columns and c in b_sel.columns]
    num_cols = [c for c in a_sel.columns if c not in key]
    merged = a_sel.merge(b_sel, on=key, how="outer", suffixes=("_a", "_b"))
    delta_cols: list[str] = []
    for c in num_cols:
        ca = f"{c}_a"
        cb = f"{c}_b"
        if ca in merged.columns and cb in merged.columns:
            delta_name = f"{c}_delta_b_minus_a"
            delta_cols.append(delta_name)
            merged[delta_name] = merged.apply(
                lambda r, ca=ca, cb=cb: "" if (r[ca] == "" or r[cb] == "") else _fmt_float(float(r[cb]) - float(r[ca]), ndigits=6),
                axis=1,
            )

    out_dir = Path(args.out) if args.out else Path("results/field_runs") / f"compare_{a_dir.name}__vs__{b_dir.name}"
    out_dir.mkdir(parents=True, exist_ok=True)
    merged.to_csv(out_dir / "compare_metrics.csv", index=False)
    delta_df = merged.loc[:, key + delta_cols] if key else merged.loc[:, delta_cols]

    md_lines: list[str] = []
    md_lines.append("# Field A/B Comparison")
    md_lines.append("")
    md_lines.append(f"- {args.label_a}: `{a_dir}`")
    md_lines.append(f"- {args.label_b}: `{b_dir}`")
    md_lines.append("")

    def _verdict_block(label: str, v: dict) -> None:
        md_lines.append(f"## KPI Verdict ({label})")
        md_lines.append("")
        md_lines.append(f"- project_verdict: `{v.get('project_verdict', 'UNKNOWN')}`")
        failed = v.get("failed") or []
        md_lines.append(f"- failed: `{failed}`")
        reason = v.get("reason", "")
        if reason:
            md_lines.append(f"- reason: `{reason}`")
        md_lines.append("")

    _verdict_block(str(args.label_a), a["verdict"])
    _verdict_block(str(args.label_b), b["verdict"])

    md_lines.append(f"## Summary Metrics ({args.label_a})")
    md_lines.append("")
    md_lines.append(_md_table(a_sel))
    md_lines.append("")
    md_lines.append(f"## Summary Metrics ({args.label_b})")
    md_lines.append("")
    md_lines.append(_md_table(b_sel))
    md_lines.append("")

    md_lines.append(f"## Delta ({args.label_b} - {args.label_a})")
    md_lines.append("")
    if key:
        delta_df = delta_df.fillna("").sort_values(key, kind="mergesort")
    else:
        delta_df = delta_df.fillna("")
    md_lines.append(_md_table(delta_df))
    md_lines.append("")

    def _kpi_table(label: str, kpi: pd.DataFrame) -> None:
        if kpi is None or kpi.empty:
            return
        md_lines.append(f"## KPI Final Table ({label})")
        md_lines.append("")
        cols = [
            c
            for c in [
                "profile",
                "sensor",
                "policy",
                "rate_improvement_vs_periodic_pct",
                "rate_improvement_vs_fixed_tau_pct",
                "recon_mae_p95_improvement_vs_fixed_tau_pct",
                "anomaly_segment_recall",
                "aoi_p95_improvement_vs_fixed_tau_pct",
                "overall",
            ]
            if c in kpi.columns
        ]
        md_lines.append(_md_table(kpi.loc[:, cols].sort_values(["profile", "sensor"], kind="mergesort")))
        md_lines.append("")

    _kpi_table(str(args.label_a), a["kpi_final"])
    _kpi_table(str(args.label_b), b["kpi_final"])

    (out_dir / "compare.md").write_text("\n".join(md_lines), encoding="utf-8")
    print(str(out_dir))


if __name__ == "__main__":
    main()
