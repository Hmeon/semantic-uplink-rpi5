"""Markdown report generation for analyzer summaries."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

from collector.kpi import compute_final_kpi
from collector.plotting_support import build_fig_basename as _fig_basename_impl
from collector.plotting_support import slugify_part as _slug_impl


def write_report_md(
    out_dir: Path,
    summary: pd.DataFrame,
    *,
    comparisons: pd.DataFrame | None = None,
    baseline_policy: str = "periodic",
    figures_dir: str = "figs",
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "report.md"
    lines = []
    lines.append("# 실험 요약 리포트")
    lines.append("")
    lines.append(
        "본 리포트는 **이벤트 기반 MAE(res)**를 사용합니다. 전체 시계열 MAE가 필요하면 "
        "원시 스트림 또는 복원 파이프가 추가로 필요합니다."
    )
    lines.append("")
    lines.append(
        "- AoI/Rate는 기본적으로 **수집기 수신 시각(`t_recv_ns`)** 기반으로 계산합니다 "
        "(없으면 `ts` 기반)."
    )
    lines.append(
        "- (optional) LinUCB reward components (`reward_aoi`, `reward_mae`, `reward_rate`) "
        "are logged only when edge diagnostics are enabled; otherwise plots are skipped."
    )
    if "n_runs" in summary.columns:
        lines.append("- 표의 `mean±std`는 **run(리플리케이트) 단위 지표**의 평균/표준편차입니다.")
        lines.append(f"- 비교 기준(baseline): `{baseline_policy}`")
    lines.append("")
    lines.append("## 지표 요약 (profile × policy × sensor)")
    lines.append("")
    # 간단한 마크다운 테이블
    tbl = summary.copy()
    # 숫자 포맷
    fmt = {
        "duration_s": "{:.1f}".format,
        "rate_Bps": "{:.1f}".format,
        "aoi_mean_ms": "{:.1f}".format,
        "aoi_p95_ms": "{:.1f}".format,
        "mae_event_mean": "{:.3f}".format,
        "mae_event_p95": "{:.3f}".format,
        "kbits_mean": "{:.2f}".format,
    }

    def _fmt_mean_std(col: str, digits: str) -> str:
        v = float(r[col])
        if not np.isfinite(v):
            return "NaN"
        std_col = f"{col}_std"
        if std_col in r and np.isfinite(float(r[std_col])):
            return f"{format(v, digits)}±{format(float(r[std_col]), digits)}"
        return format(v, digits)

    def _fmt_pct_mean_std(col: str, digits: str) -> str:
        """ratio(0..1) 컬럼을 %로 표시."""
        v = float(r[col])
        if not np.isfinite(v):
            return "NaN"
        v_pct = v * 100.0
        std_col = f"{col}_std"
        if std_col in r and np.isfinite(float(r[std_col])):
            return f"{format(v_pct, digits)}±{format(float(r[std_col]) * 100.0, digits)}"
        return format(v_pct, digits)
    # n_runs가 있으면 함께 표시
    has_runs = "n_runs" in tbl.columns
    if has_runs:
        lines.append(
            "| profile | policy | sensor | n_runs | n_events | dur[s] | rate[B/s] | AoI_mean[ms] | "
            "AoI_p95[ms] | MAE_event_mean | MAE_event_p95 | k̄ |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    else:
        lines.append(
            "| profile | policy | sensor | n_events | dur[s] | rate[B/s] | AoI_mean[ms] | "
            "AoI_p95[ms] | MAE_event_mean | MAE_event_p95 | k̄ |"
        )
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")

    def _fmt_cell(row: pd.Series, col: str) -> str:
        v = float(row[col])
        if not np.isfinite(v):
            return "NaN"
        return fmt[col](v)

    for _, r in tbl.iterrows():
        if has_runs:
            cells = [
                str(r["profile"]),
                str(r["policy"]),
                str(r["sensor"]),
                str(int(r.get("n_runs", 0))),
                str(int(r["n_events"])),
                _fmt_mean_std("duration_s", ".1f"),
                _fmt_mean_std("rate_Bps", ".1f"),
                _fmt_mean_std("aoi_mean_ms", ".1f"),
                _fmt_mean_std("aoi_p95_ms", ".1f"),
                _fmt_mean_std("mae_event_mean", ".3f"),
                _fmt_mean_std("mae_event_p95", ".3f"),
                _fmt_mean_std("kbits_mean", ".2f"),
            ]
        else:
            cells = [
                str(r["profile"]),
                str(r["policy"]),
                str(r["sensor"]),
                str(int(r["n_events"])),
                _fmt_cell(r, "duration_s"),
                _fmt_cell(r, "rate_Bps"),
                _fmt_cell(r, "aoi_mean_ms"),
                _fmt_cell(r, "aoi_p95_ms"),
                _fmt_cell(r, "mae_event_mean"),
                _fmt_cell(r, "mae_event_p95"),
                _fmt_cell(r, "kbits_mean"),
            ]

        lines.append("| " + " | ".join(cells) + " |")

    # --- Seq-aligned quality (vs periodic) ---
    qual_cols = [
        "recon_mae_mean",
        "recon_mae_p95",
        "recon_mae_max",
        "anomaly_tau_ref",
        "anomaly_segment_recall",
        "anomaly_segments",
        "anomaly_segments_hit",
    ]
    if any(c in tbl.columns for c in qual_cols):
        lines.append("")
        lines.append("## Quality (seq-aligned vs periodic)")
        lines.append("")
        lines.append(
            "- `recon_mae_*`: periodic baseline(=전부 전송) 스트림을 **센서별 `seq`로 정렬**한 뒤, "
            "후보 정책 스트림을 LOCF(마지막 전송값 유지)로 복원하여 MAE를 계산합니다."
        )
        lines.append("- `anomaly_segment_recall`:")
        lines.append("  - periodic baseline에서 `|res| > tau_ref` 세그먼트(길이>=2샘플)를 정의")
        lines.append("  - 후보 정책이 세그먼트 내 1회 이상 전송하면 hit")
        lines.append("  - recall = hit / segments")
        lines.append("  - (convention) segments=0 => recall=1.0 (vacuously satisfied)")
        lines.append("")

        if has_runs:
            lines.append(
                "| profile | policy | sensor | recon_mean | recon_p95 | recon_max | "
                "tau_ref | anomaly_recall | hit/total |"
            )
            lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
        else:
            lines.append(
                "| profile | policy | sensor | recon_mean | recon_p95 | recon_max | "
                "tau_ref | anomaly_recall | hit/total |"
            )
            lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")

        def _fmt_float(v: float, digits: str) -> str:
            if not np.isfinite(v):
                return "NaN"
            return format(float(v), digits)

        def _fmt_int(col: str) -> str:
            v = float(r.get(col, float("nan")))
            if not np.isfinite(v):
                return "NaN"
            return str(int(round(v)))

        for _, r in tbl.iterrows():
            recon_mean = (
                _fmt_mean_std("recon_mae_mean", ".3f")
                if ("recon_mae_mean" in r and has_runs)
                else _fmt_float(float(r.get("recon_mae_mean", float("nan"))), ".3f")
            )
            recon_p95 = (
                _fmt_mean_std("recon_mae_p95", ".3f")
                if ("recon_mae_p95" in r and has_runs)
                else _fmt_float(float(r.get("recon_mae_p95", float("nan"))), ".3f")
            )
            recon_max = (
                _fmt_mean_std("recon_mae_max", ".3f")
                if ("recon_mae_max" in r and has_runs)
                else _fmt_float(float(r.get("recon_mae_max", float("nan"))), ".3f")
            )
            tau_ref = (
                _fmt_mean_std("anomaly_tau_ref", ".3f")
                if ("anomaly_tau_ref" in r and has_runs)
                else _fmt_float(float(r.get("anomaly_tau_ref", float("nan"))), ".3f")
            )
            recall = (
                _fmt_mean_std("anomaly_segment_recall", ".3f")
                if ("anomaly_segment_recall" in r and has_runs)
                else _fmt_float(float(r.get("anomaly_segment_recall", float("nan"))), ".3f")
            )
            hit = _fmt_int("anomaly_segments_hit")
            total = _fmt_int("anomaly_segments")
            hit_total = "NaN"
            if hit != "NaN" and total != "NaN":
                hit_total = f"{hit}/{total}"

            cells = [
                str(r["profile"]),
                str(r["policy"]),
                str(r["sensor"]),
                recon_mean,
                recon_p95,
                recon_max,
                tau_ref,
                recall,
                hit_total,
            ]
            lines.append("| " + " | ".join(cells) + " |")

    # --- LinUCB diagnostics (optional) ---
    if "policy" in tbl.columns:
        diag = tbl[tbl["policy"].astype("string") == "adaptive"].copy()
    else:
        diag = pd.DataFrame()

    if not diag.empty:
        lines.append("")
        lines.append("## LinUCB/파이프라인 진단 (adaptive)")
        lines.append("")

        has_decisions = "linucb_n_decisions" in diag.columns
        if not has_decisions:
            lines.append(
                "- Decision 로그(`decisions_*.parquet|csv`)가 없어 decision 기반 LinUCB 진단이 "
                "비어 있습니다."
            )
            lines.append(
                "  - Edge: `--decision-publish event`(또는 `always`)로 decision 메시지를 발행해야 "
                "합니다."
            )
            lines.append(
                "  - Synthetic generator: `--decision-publish event`로 decision 로그를 생성하세요."
            )
            lines.append("")

        # Always show event-observable action diversity metrics when available.
        show_actions = {"action_unique_count", "action_switch_rate"}.issubset(diag.columns)

        if has_decisions:
            cols = [
                "profile",
                "sensor",
                "actions",
                "action_switch",
                "n_dec",
                "H(60s)",
                "switch",
                "safe_forced",
                "AOI_limit",
                "MAE_limit",
                "BOTH",
                "UCB_u_mean",
                "skip/dec",
                "q_max",
                "q_auc[count·s]",
                "q_recover[s]",
                "dup_bytes_ratio[%]",
                "rx_p50[ms]",
                "rx_p95[ms]",
            ]
        else:
            cols = [
                "profile",
                "sensor",
                "actions",
                "action_switch",
                "dup_bytes_ratio[%]",
                "rx_p50[ms]",
                "rx_p95[ms]",
            ]
        header = "| " + " | ".join(cols) + " |"
        # First two columns are identifiers, the rest are numeric-ish.
        sep = "|" + "|".join(["---", "---"] + ["---:"] * (len(cols) - 2)) + "|"
        lines.append(header)
        lines.append(sep)

        for _, r in diag.iterrows():
            actions = (
                _fmt_mean_std("action_unique_count", ".0f")
                if show_actions and "action_unique_count" in r
                else "NaN"
            )
            action_switch = (
                _fmt_mean_std("action_switch_rate", ".3f")
                if show_actions and "action_switch_rate" in r
                else "NaN"
            )
            dup = _fmt_pct_mean_std("dup_bytes_ratio", ".1f") if "dup_bytes_ratio" in r else "NaN"
            rx_p50 = _fmt_mean_std("rx_delay_p50_ms", ".1f") if "rx_delay_p50_ms" in r else "NaN"
            rx_p95 = _fmt_mean_std("rx_delay_p95_ms", ".1f") if "rx_delay_p95_ms" in r else "NaN"

            if not has_decisions:
                cells = [
                    str(r["profile"]),
                    str(r["sensor"]),
                    actions,
                    action_switch,
                    dup,
                    rx_p50,
                    rx_p95,
                ]
                lines.append("| " + " | ".join(cells) + " |")
                continue

            if "linucb_n_decisions" in r:
                n_dec = _fmt_mean_std("linucb_n_decisions", ".0f")
            else:
                n_dec = "NaN"
            h_60s = (
                _fmt_mean_std("linucb_action_entropy_mean_60s", ".3f")
                if "linucb_action_entropy_mean_60s" in r
                else "NaN"
            )
            switch = (
                _fmt_mean_std("linucb_switch_rate", ".3f") if "linucb_switch_rate" in r else "NaN"
            )
            safe_forced = (
                _fmt_mean_std("linucb_safe_forced_rate", ".3f")
                if "linucb_safe_forced_rate" in r
                else "NaN"
            )
            aoi_limit = (
                _fmt_mean_std("linucb_forced_reason_aoi_limit_rate", ".3f")
                if "linucb_forced_reason_aoi_limit_rate" in r
                else "NaN"
            )
            mae_limit = (
                _fmt_mean_std("linucb_forced_reason_mae_limit_rate", ".3f")
                if "linucb_forced_reason_mae_limit_rate" in r
                else "NaN"
            )
            both = (
                _fmt_mean_std("linucb_forced_reason_both_rate", ".3f")
                if "linucb_forced_reason_both_rate" in r
                else "NaN"
            )
            u_mean = (
                _fmt_mean_std("linucb_ucb_uncertainty_mean", ".3f")
                if "linucb_ucb_uncertainty_mean" in r
                else "NaN"
            )
            skip_dec = (
                _fmt_mean_std("linucb_rate_limit_skips_per_decision", ".3f")
                if "linucb_rate_limit_skips_per_decision" in r
                else "NaN"
            )
            if "outbox_pending_max" in r:
                q_max = _fmt_mean_std("outbox_pending_max", ".1f")
            else:
                q_max = "NaN"
            if "outbox_pending_auc_s" in r:
                q_auc = _fmt_mean_std("outbox_pending_auc_s", ".1f")
            else:
                q_auc = "NaN"
            q_rec = (
                _fmt_mean_std("outbox_pending_recovery_s", ".1f")
                if "outbox_pending_recovery_s" in r
                else "NaN"
            )
            cells = [
                str(r["profile"]),
                str(r["sensor"]),
                actions,
                action_switch,
                n_dec,
                h_60s,
                switch,
                safe_forced,
                aoi_limit,
                mae_limit,
                both,
                u_mean,
                skip_dec,
                q_max,
                q_auc,
                q_rec,
                dup,
                rx_p50,
                rx_p95,
            ]
            lines.append("| " + " | ".join(cells) + " |")
    p.write_text("\n".join(lines), encoding="utf-8")

    # --- Baseline 비교(추가) ---
    if comparisons is None or comparisons.empty:
        return

    lines = p.read_text(encoding="utf-8").splitlines()
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"## {baseline_policy} 대비 변화(정량 비교)")
    lines.append("")
    lines.append(
        "개선율(%) 정의(낮을수록 좋은 지표 기준): "
        "`improvement = (baseline - candidate) / baseline * 100`"
    )
    lines.append("")
    lines.append(
        "| profile | sensor | policy | ΔRate[B/s] | Rate 개선율[%] | ΔAoIμ[ms] | AoIμ 개선율[%] | "
        "ΔMAE | MAE 개선율[%] |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")

    def _pct(v: float) -> str:
        if not math.isfinite(v):
            return "NaN"
        return f"{v:+.1f}"

    def _num(v: float, fmt_s: str) -> str:
        if not math.isfinite(v):
            return "NaN"
        return format(v, fmt_s)

    for _, r in comparisons.iterrows():
        d_rate = _num(float(r.get("rate_Bps_delta_Bps", float("nan"))), "+.1f")
        p_rate = _pct(float(r.get("rate_Bps_improvement_pct", float("nan"))))
        d_aoi = _num(float(r.get("aoi_mean_ms_delta_ms", float("nan"))), "+.1f")
        p_aoi = _pct(float(r.get("aoi_mean_ms_improvement_pct", float("nan"))))
        d_mae = _num(float(r.get("mae_event_mean_delta_mae", float("nan"))), "+.3f")
        p_mae = _pct(float(r.get("mae_event_mean_improvement_pct", float("nan"))))

        cells = [
            str(r["profile"]),
            str(r["sensor"]),
            str(r["policy"]),
            d_rate,
            p_rate,
            d_aoi,
            p_aoi,
            d_mae,
            p_mae,
        ]
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    lines.append("## KPI 최종 확정안 (PASS/FAIL, strict)")
    lines.append("")
    lines.append("- 평가 범위(강제): profile × sensor 단위")
    lines.append("  - 모든 row PASS여야 프로젝트 PASS (부분 합격 없음)")
    lines.append("- Baselines (fixed):")
    lines.append("  - periodic: 최빈 송신(참조 스트림)")
    lines.append("  - fixed_tau: 사람이 설정한 고정 임계치(품질 기준점)")
    lines.append("  - adaptive: LinUCB 정책(합격 판정 대상)")
    lines.append("- PASS 조건(5개 전부):")
    lines.append("  - K1 Rate_improvement_vs_periodic >= 85%")
    lines.append("  - K2 Rate_improvement_vs_fixed_tau >= -10%")
    lines.append("  - K3 recon_mae_p95_improvement_vs_fixed_tau >= -10%")
    lines.append("  - K4 AnomalySegmentRecall >= 0.90")
    lines.append("  - K5 AoI_p95_improvement_vs_fixed_tau >= -10%")
    lines.append("- AnomalySegmentRecall 정의:")
    lines.append("  - periodic baseline에서 |res| > tau_ref(=fixed_tau 중앙값 tau)")
    lines.append("  - 길이>=2샘플 세그먼트 hit-rate")
    lines.append("")
    lines.append(
        "| profile | sensor | policy | K1 | K2 | K3 | K4 | K5 | Overall |"
    )
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    kpi, project_pass = compute_final_kpi(summary)
    kpi_available = not kpi.empty

    def _fmt_pct(status: str, val: float) -> str:
        if not math.isfinite(val):
            return "FAIL (NaN)"
        return f"{status} ({val:+.1f}%)"

    def _fmt_recall(status: str, val: float) -> str:
        if not math.isfinite(val):
            return "FAIL (NaN)"
        return f"{status} ({val:.3f})"

    failed_pairs: list[tuple[str, str]] = []
    if kpi_available:
        for _, r in kpi.iterrows():
            prof = str(r.get("profile", ""))
            sensor = str(r.get("sensor", ""))
            pol = str(r.get("policy", "adaptive"))
            if str(r.get("overall", "FAIL")) != "PASS":
                failed_pairs.append((prof, sensor))

            c1 = _fmt_pct(
                str(r.get("kpi1_rate_vs_periodic", "FAIL")),
                float(r.get("rate_improvement_vs_periodic_pct", float("nan"))),
            )
            c2 = _fmt_pct(
                str(r.get("kpi2_rate_vs_fixed_tau", "FAIL")),
                float(r.get("rate_improvement_vs_fixed_tau_pct", float("nan"))),
            )
            c3 = _fmt_pct(
                str(r.get("kpi3_recon_p95_vs_fixed_tau", "FAIL")),
                float(r.get("recon_mae_p95_improvement_vs_fixed_tau_pct", float("nan"))),
            )
            c4 = _fmt_recall(
                str(r.get("kpi4_anomaly_segment_recall", "FAIL")),
                float(r.get("anomaly_segment_recall", float("nan"))),
            )
            c5 = _fmt_pct(
                str(r.get("kpi5_aoi_p95_vs_fixed_tau", "FAIL")),
                float(r.get("aoi_p95_improvement_vs_fixed_tau_pct", float("nan"))),
            )
            overall = str(r.get("overall", "FAIL"))

            cells = [prof, sensor, pol, c1, c2, c3, c4, c5, overall]
            lines.append("| " + " | ".join(cells) + " |")
    else:
        lines.append("| - | - | adaptive | SKIP | SKIP | SKIP | SKIP | SKIP | SKIP |")

    lines.append("")
    lines.append("### Project verdict")
    lines.append("")
    verdict = "PASS" if project_pass else ("FAIL" if kpi_available else "SKIP")
    lines.append(f"- Verdict: **{verdict}**")
    if verdict == "SKIP":
        lines.append("- Reason: no `adaptive` policy rows found in inputs; KPI is not applicable.")
    else:
        if failed_pairs:
            items = ", ".join([f"{p}/{s}" for (p, s) in failed_pairs])
            lines.append(f"- Failed profile×sensor: {items}")

        lines.append("")
        lines.append("### Interpretation")
        lines.append("")
        lines.append("- 1순위(Primary): periodic 대비 Rate 절감(↑)을 확인합니다.")
        lines.append("- 제약(Constraints): fixed_tau 대비 Rate/Recon_p95/AoI_p95")
        lines.append("  - 10% 초과 악화(개선율 < -10%)면 FAIL")
        lines.append("- 커버리지: AnomalySegmentRecall(↑)로 이상구간 미스 여부를 확인합니다.")

    # --- Figures (추가) ---
    figs_path = out_dir / figures_dir
    if figs_path.exists():
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("## Figures (시각화)")
        lines.append("")
        lines.append(f"- 생성 경로: `{figures_dir}/`")
        lines.append("")

        # profile×sensor별로 핵심 그림 4개를 나열(논문/보고서에 바로 붙이기 용)
        for (prof, sensor), _g in summary.groupby(["profile", "sensor"], sort=False):
            prof_s = str(prof)
            sensor_s = str(sensor)
            lines.append(f"### {prof_s} / {sensor_s}")
            lines.append("")
            rate_fig = _fig_basename(
                sensor=sensor_s,
                profile=prof_s,
                policy="compare",
                metric="rate_bar",
            )
            aoi_p95_fig = _fig_basename(
                sensor=sensor_s,
                profile=prof_s,
                policy="compare",
                metric="aoi_p95_bar",
            )
            mae_p95_fig = _fig_basename(
                sensor=sensor_s,
                profile=prof_s,
                policy="compare",
                metric="mae_p95_bar",
            )
            pareto_fig = _fig_basename(
                sensor=sensor_s,
                profile=prof_s,
                policy="compare",
                metric="pareto_rate_vs_aoi_mean",
            )
            reward_components_fig = _fig_basename(
                sensor=sensor_s,
                profile=prof_s,
                policy="compare",
                metric="reward_components_bar",
            )
            for rel in [
                f"{figures_dir}/{rate_fig}.png",
                f"{figures_dir}/{aoi_p95_fig}.png",
                f"{figures_dir}/{mae_p95_fig}.png",
                f"{figures_dir}/{pareto_fig}.png",
                f"{figures_dir}/{reward_components_fig}.png",
            ]:
                if (out_dir / rel).exists():
                    lines.append(f"![]({rel})")
            lines.append("")

        # paper-plots(추가): 논문/최종보고서용 플롯이 있으면 함께 임베드
        paper_any = any(figs_path.glob("*_env_metrics_panel.png")) or any(
            figs_path.glob("*_action_heatmap.png")
        )
        paper_any = paper_any or any(figs_path.glob("*_feature_weights__*.png"))
        paper_any = paper_any or any(figs_path.glob("*_reward_ts__*.png"))
        paper_any = paper_any or any(figs_path.glob("*_cumulative_regret__*.png"))
        paper_any = paper_any or any(figs_path.glob("*_stability_abs_res_ts__*.png"))
        paper_any = paper_any or any(figs_path.glob("*_timeline__*.png"))
        if paper_any:
            lines.append("---")
            lines.append("")
            lines.append("## Paper Figures (논문용 추가 플롯)")
            lines.append("")

            # sensor 단위 환경 비교(Reward/최종지표)
            for sensor_s in sorted({str(s) for s in summary["sensor"].unique()}):
                env_metrics = _fig_basename(
                    sensor=sensor_s,
                    profile="all",
                    policy="compare",
                    metric="env_metrics_panel",
                )
                env_reward = _fig_basename(
                    sensor=sensor_s,
                    profile="all",
                    policy="adaptive",
                    metric="reward_by_profile_ts",
                )
                for rel in [
                    f"{figures_dir}/{env_metrics}.png",
                    f"{figures_dir}/{env_reward}.png",
                ]:
                    if (out_dir / rel).exists():
                        lines.append(f"![]({rel})")
                lines.append("")

            # profile×sensor 단위: action heatmap + 대표 run의 θ/reward/regret/timeline
            for (prof, sensor), _g in summary.groupby(["profile", "sensor"], sort=False):
                prof_s = str(prof)
                sensor_s = str(sensor)
                lines.append(f"### {prof_s} / {sensor_s}")
                lines.append("")

                heatmap = _fig_basename(
                    sensor=sensor_s,
                    profile=prof_s,
                    policy="adaptive",
                    metric="action_heatmap",
                )
                rel = f"{figures_dir}/{heatmap}.png"
                if (out_dir / rel).exists():
                    lines.append(f"![]({rel})")

                sensor_slug = _slug(sensor_s)
                prof_slug = _slug(prof_s)
                for pattern in [
                    f"{sensor_slug}_{prof_slug}_adaptive_feature_weights__*.png",
                    f"{sensor_slug}_{prof_slug}_adaptive_reward_ts__*.png",
                    f"{sensor_slug}_{prof_slug}_adaptive_cumulative_regret__*.png",
                    f"{sensor_slug}_{prof_slug}_adaptive_stability_abs_res_ts__*.png",
                    f"{sensor_slug}_{prof_slug}_adaptive_timeline__*.png",
                ]:
                    matches = sorted(figs_path.glob(pattern))
                    if matches:
                        lines.append(f"![]({figures_dir}/{matches[0].name})")
                lines.append("")

    p.write_text("\n".join(lines), encoding="utf-8")



def _slug(x: str) -> str:
    return _slug_impl(x)


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


__all__ = ["write_report_md"]

