# collector/analyze.py
# Python 3.10+
# 목적: 수집된 Event 로그를 읽어 Rate/AoI/MAE를 시나리오별로 집계하고,
#       표/파레토 분석에 필요한 요약 CSV/Parquet/Markdown을 생성한다.
#
# 입력 기대:
#  - Parquet: *.parquet (권장, 빠름)
#  - CSV    : *.csv (utf-8, 헤더 포함)
#  - 파일/디렉터리 모두 허용. 디렉터리는 재귀 검색하며
#    events_*.parquet(회전) / events.parquet(레거시) 우선.
#  - 필수 컬럼(스키마 정합, 표준화 후):
#      ts(ns), seq, device_id, sensor, val, pred, res, tau, kbits, profile, policy
#    * Collector가 저장한 events*.parquet는 ts 대신 ts_ns를 쓰는 경우가 있어 자동 변환한다.
#  - (권장) 수집기 기준 AoI/Rate 계산을 위해 t_recv_ns가 있으면 사용한다.
#  - (선택) 네트워크 사용량 추정을 위해 mqtt_bytes 또는 mqtt_size_bytes가 있으면 사용한다.
#
# 지표 정의:
#  - Rate(브로커 수신 바이트/초) = Σ MQTT_PUBLISH_추정바이트 / 관측시간[s]
#      → payload는 EventMsg JSON 직렬화, 헤더 포함(v3.1.1). (common.mqttutil 기반)
#  - AoI 평균/95번째백분위(ms): *연속시간 AoI 분포*를 계산한다.
#      - t_recv_ns가 있을 때(권장): 생성시각(ts)과 수신시각(t_recv_ns)로 수집기 관점 AoI 계산
#      - 없을 때: 이벤트 간 간격 Δ_i 기반(지연=0 가정)으로 계산
#  - MAE(이벤트 기반): res의 평균/분위수(잔차는 |x - pred|). *전체 시계열 MAE가 아니라
#    이벤트 시점의 오차 평균임을 리포트에 명시.*
#
# 산출물:
#  - metrics_summary.(csv|parquet)
#  - report.md (간단 비교표)
#  - (옵션) pareto_<sensor>.csv (Rate vs AoI 테이블)
#
# 의존: pandas, numpy, pyarrow(옵션), common.schema.EventMsg, common.mqttutil

"""Offline analyzer for collector logs (Rate/AoI/MAE).

Loads event/decision/marker logs, normalizes schema, and produces summary tables
plus a Markdown report. AoI/Rate are computed from receiver time when available,
which is a deliberate choice that affects all downstream comparisons.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from collector.decision_diagnostics import (
    summarize_decisions_diagnostics_by_run as _summarize_decisions_diagnostics_by_run_impl,
)
from collector.kpi import compare_policies as _compare_policies_impl
from collector.kpi import compute_final_kpi as _compute_final_kpi_impl
from collector.kpi import summarize as _summarize_impl
from collector.load_normalize import (
    discover_files as _discover_files_impl,
)
from collector.load_normalize import (
    discover_named_files as _discover_named_files_impl,
)
from collector.load_normalize import (
    enrich_decisions_with_events as _enrich_decisions_with_events_impl,
)
from collector.load_normalize import extract_run_meta as _extract_run_meta_impl
from collector.load_normalize import (
    infer_profile_policy_from_path as _infer_profile_policy_from_path_impl,
)
from collector.load_normalize import infer_run_dir_from_file as _infer_run_dir_from_file_impl
from collector.load_normalize import infer_run_id_from_path as _infer_run_id_from_path_impl
from collector.load_normalize import load_collector_meta as _load_collector_meta_impl
from collector.load_normalize import load_decisions as _load_decisions_impl
from collector.load_normalize import load_events as _load_events_impl
from collector.load_normalize import (
    normalize_decisions_schema as _normalize_decisions_schema_impl,
)
from collector.load_normalize import normalize_events_schema as _normalize_events_schema_impl
from collector.load_normalize import read_json_best_effort as _read_json_best_effort_impl
from collector.metrics_core import (
    aoi_mean_and_p95 as _aoi_mean_and_p95_impl,
)
from collector.metrics_core import (
    aoi_mean_and_p95_from_rx as _aoi_mean_and_p95_from_rx_impl,
)
from collector.metrics_core import dedup_and_sort as _dedup_and_sort_impl
from collector.metrics_core import estimate_payload_bytes as _estimate_payload_bytes_impl
from collector.metrics_core import summarize_by_run as _summarize_by_run_impl
from collector.plot_config import PlotConfig
from collector.plot_config import parse_plot_formats as _parse_plot_formats_impl
from collector.plot_generators import (
    _try_make_diagnostic_plots as _try_make_diagnostic_plots_impl,
)
from collector.plot_generators import _try_make_paper_plots as _try_make_paper_plots_impl
from collector.plot_generators import (
    _try_make_pipeline_plots as _try_make_pipeline_plots_impl,
)
from collector.plot_generators import _try_make_plots as _try_make_plots_impl
from collector.plot_orchestrator import generate_plots as _generate_plots_impl
from collector.plot_runtime import apply_plot_style as _apply_plot_style_impl
from collector.plot_runtime import maybe_import_matplotlib as _maybe_import_matplotlib_impl
from collector.plot_runtime import write_plot_manifest as _write_plot_manifest_impl
from collector.plotting_support import PLOT_MANIFEST as _PLOT_MANIFEST
from collector.plotting_support import build_fig_basename as _fig_basename_impl
from collector.plotting_support import clear_plot_manifest as _clear_plot_manifest
from collector.plotting_support import save_figure_multi as _save_figure_multi_impl
from collector.plotting_support import slugify_part as _slug_impl
from collector.quality_metrics import (
    compute_seq_aligned_quality_metrics as _compute_seq_aligned_quality_metrics_impl,
)
from collector.reporting import write_report_md as _write_report_md_impl
from common.discord_webhook import DiscordWebhookError, send_discord_message
from common.logging_setup import add_logging_cli_args, setup_logging_from_args

logger = logging.getLogger(__name__)

# ----------------------------- I/O -----------------------------

def _infer_run_dir_from_file(p: Path) -> Path:
    return _infer_run_dir_from_file_impl(p)


def _read_json_best_effort(p: Path) -> dict | None:
    return _read_json_best_effort_impl(p)


def _extract_run_meta(run_dir: Path) -> dict[str, object]:
    return _extract_run_meta_impl(run_dir)


def _discover_files(inputs: Iterable[str | os.PathLike]) -> list[Path]:
    return _discover_files_impl(inputs)


def _discover_named_files(
    inputs: Iterable[str | os.PathLike], *, names: Iterable[str]
) -> list[Path]:
    return _discover_named_files_impl(inputs, names=names)


def load_events(paths: list[str | os.PathLike]) -> pd.DataFrame:
    return _load_events_impl(paths)


def load_decisions(paths: list[str | os.PathLike]) -> pd.DataFrame:
    return _load_decisions_impl(paths)


def load_collector_meta(paths: list[str | os.PathLike]) -> pd.DataFrame:
    return _load_collector_meta_impl(paths)


def _normalize_decisions_schema(df: pd.DataFrame) -> pd.DataFrame:
    return _normalize_decisions_schema_impl(df)


def enrich_decisions_with_events(decisions: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    return _enrich_decisions_with_events_impl(decisions, events)


def summarize_decisions_diagnostics_by_run(
    decisions: pd.DataFrame,
    *,
    window_s: int = 60,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    return _summarize_decisions_diagnostics_by_run_impl(decisions, window_s=window_s)


def _normalize_events_schema(df: pd.DataFrame) -> pd.DataFrame:
    return _normalize_events_schema_impl(df)


def _infer_profile_policy_from_path(p: Path) -> tuple[str, str]:
    return _infer_profile_policy_from_path_impl(p)


def _infer_run_id_from_path(p: Path) -> str:
    return _infer_run_id_from_path_impl(p)

# ------------------------- 전처리/유틸 -------------------------

def dedup_and_sort(df: pd.DataFrame) -> pd.DataFrame:
    return _dedup_and_sort_impl(df)


def estimate_payload_bytes(df: pd.DataFrame) -> pd.Series:
    return _estimate_payload_bytes_impl(df)


def aoi_mean_and_p95_from_rx(gen_ns: np.ndarray, recv_ns: np.ndarray) -> tuple[float, float]:
    return _aoi_mean_and_p95_from_rx_impl(gen_ns, recv_ns)


def aoi_mean_and_p95(ts_ns: np.ndarray) -> tuple[float, float]:
    return _aoi_mean_and_p95_impl(ts_ns)


def summarize_by_run(df: pd.DataFrame) -> pd.DataFrame:
    return _summarize_by_run_impl(df)

def compute_seq_aligned_quality_metrics(
    df: pd.DataFrame,
    *,
    baseline_policy: str = "periodic",
    tau_ref_policy: str = "fixed_tau",
) -> pd.DataFrame:
    return _compute_seq_aligned_quality_metrics_impl(
        df,
        baseline_policy=baseline_policy,
        tau_ref_policy=tau_ref_policy,
    )

def summarize(summary_by_run: pd.DataFrame) -> pd.DataFrame:
    """Backward-compatible wrapper around collector.kpi.summarize."""
    return _summarize_impl(summary_by_run)


def compare_policies(
    summary: pd.DataFrame,
    *,
    baseline_policy: str = "periodic",
) -> pd.DataFrame:
    """Backward-compatible wrapper around collector.kpi.compare_policies."""
    return _compare_policies_impl(summary, baseline_policy=baseline_policy)


def compute_final_kpi(summary: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    """Backward-compatible wrapper around collector.kpi.compute_final_kpi."""
    return _compute_final_kpi_impl(summary)


# --------------------------- 리포트 ---------------------------

def _write_report_md(
    out_dir: Path,
    summary: pd.DataFrame,
    *,
    comparisons: pd.DataFrame | None = None,
    baseline_policy: str = "periodic",
    figures_dir: str = "figs",
) -> None:
    """Backward-compatible wrapper around collector.reporting.write_report_md."""
    return _write_report_md_impl(
        out_dir,
        summary,
        comparisons=comparisons,
        baseline_policy=baseline_policy,
        figures_dir=figures_dir,
    )


# --------------------------- Plotting ---------------------------

def _parse_plot_formats(raw: str) -> tuple[str, ...]:
    return _parse_plot_formats_impl(raw)


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
    return _try_make_plots_impl(
        out_dir,
        summary,
        plot_cfg=plot_cfg,
        pareto_p95=pareto_p95,
    )


def _try_make_pipeline_plots(
    out_dir: Path,
    *,
    events: pd.DataFrame,
    decisions_enriched: pd.DataFrame,
    by_run: pd.DataFrame,
    plot_cfg: PlotConfig,
) -> list[Path]:
    return _try_make_pipeline_plots_impl(
        out_dir,
        events=events,
        decisions_enriched=decisions_enriched,
        by_run=by_run,
        plot_cfg=plot_cfg,
    )


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
    return _try_make_paper_plots_impl(
        out_dir,
        events=events,
        decisions=decisions,
        summary=summary,
        plot_cfg=plot_cfg,
        policy_config_path=policy_config_path,
        reward_window=reward_window,
        action_bins=action_bins,
        top_actions=top_actions,
        cellular_var_period_s=cellular_var_period_s,
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
    return _try_make_diagnostic_plots_impl(
        out_dir,
        events=events,
        decisions_enriched=decisions_enriched,
        by_run=by_run,
        summary=summary,
        arm_distribution=arm_distribution,
        entropy_windows=entropy_windows,
        plot_cfg=plot_cfg,
        arm_top_n=arm_top_n,
        entropy_smooth_window=entropy_smooth_window,
        ucb_timeseries=ucb_timeseries,
    )

def _fmt_num(value, fmt: str) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "NaN"
    if not math.isfinite(num):
        return "NaN"
    return format(num, fmt)


def format_summary_for_discord(summary: pd.DataFrame, *, limit: int = 10) -> str:
    """Format a compact Discord-ready summary.

    Args:
        summary: Aggregated metrics from `summarize`.
        limit: Max number of rows to include in the message.

    Returns:
        A Markdown string suitable for Discord webhook payloads.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Assumes summary columns match the analyzer output schema.

    Failure Modes:
        - Missing columns are rendered as placeholders rather than raising.
    """

    title = "**Semantic Uplink 분석 요약**"
    if summary.empty:
        return f"{title}\n집계된 행이 없어 전송할 내용이 없습니다."

    lines = [title]
    lines.append(f"총 {len(summary)}행 중 상위 {min(limit, len(summary))}행을 전송합니다.")

    subset = summary.head(limit)
    for _, row in subset.iterrows():
        profile = row.get("profile", "?")
        policy = row.get("policy", "?")
        sensor = row.get("sensor", "?")
        events = int(row.get("n_events", 0))
        rate = _fmt_num(row.get("rate_Bps"), ".1f")
        aoi_mean = _fmt_num(row.get("aoi_mean_ms"), ".1f")
        aoi_p95 = _fmt_num(row.get("aoi_p95_ms"), ".1f")
        mae_mean = _fmt_num(row.get("mae_event_mean"), ".3f")
        mae_p95 = _fmt_num(row.get("mae_event_p95"), ".3f")
        kbits = _fmt_num(row.get("kbits_mean"), ".2f")
        lines.append(
            f"- `{profile}/{policy}` sensor={sensor} · events={events} · rate={rate} B/s · "
            f"AoIμ={aoi_mean} ms (p95={aoi_p95} ms) · "
            f"MAE={mae_mean} (p95={mae_p95}) · k̄={kbits}"
        )

    if len(summary) > limit:
        lines.append(f"… (총 {len(summary)}행 중 {limit}행만 표시)")

    return "\n".join(lines)


# ----------------------------- CLI -----------------------------

def parse_args():
    """Parse CLI arguments for the analyzer.

    Args:
        None.

    Returns:
        Parsed argparse namespace with analysis parameters.

    Raises:
        SystemExit: If CLI arguments are invalid.

    Side Effects:
        - None (argparse may print to stderr on failure).

    Contract:
        - Ensures required input paths are provided.

    Failure Modes:
        - Argument parsing errors exit the process.
    """
    ap = argparse.ArgumentParser(description="Analyze semantic uplink experiments (Rate/AoI/MAE)")
    ap.add_argument("--input", "-i", action="append", required=True,
                    help="분석할 파일 또는 디렉터리 (여러 번 지정 가능)")
    ap.add_argument("--out", "-o", default="artifacts/analysis",
                    help="요약 결과 출력 디렉터리")
    ap.add_argument("--save-parquet", action="store_true",
                    help="metrics_summary.parquet도 함께 저장")
    ap.add_argument("--baseline-policy", default="periodic",
                    help="비교 기준 정책 (default: periodic)")
    ap.add_argument(
        "--plots",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="시각화(figs/) 생성 (default: enabled)",
    )
    ap.add_argument(
        "--plot-dir",
        default="figs",
        help="(plots) out 하위 그림 디렉터리명 (default: figs/)",
    )
    ap.add_argument(
        "--plot-formats",
        default="png,pdf",
        help="(plots) 저장 포맷(콤마 구분). 예: png,pdf / png,svg (default: png,pdf)",
    )
    ap.add_argument(
        "--plot-dpi",
        type=int,
        default=300,
        help="(plots) PNG DPI (default: 300)",
    )
    ap.add_argument(
        "--pareto-p95",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="(plots) Pareto: Rate vs AoI p95도 추가 생성 (default: disabled)",
    )
    ap.add_argument(
        "--diagnostic-plots",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="(plots) LinUCB/파이프라인 진단 플롯 생성 (default: disabled)",
    )
    ap.add_argument(
        "--ucb-timeseries",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="(diagnostic-plots) UCB 분해값 time-series도 생성 (default: disabled)",
    )
    ap.add_argument(
        "--entropy-smooth-window",
        type=int,
        default=0,
        help="(diagnostic-plots) entropy rolling mean 윈도우(창 개수). 0이면 off",
    )
    ap.add_argument(
        "--arm-top-n",
        type=int,
        default=12,
        help="(diagnostic-plots) arm 분포에서 상위 N만 표시(나머지는 others). <=0이면 전체",
    )
    ap.add_argument(
        "--paper-plots",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="논문/최종보고서용 추가 플롯 생성 (default: enabled)",
    )
    ap.add_argument(
        "--policy-config",
        default="configs/policy.yaml",
        help="(paper-plots) safety/arms 설정 YAML 경로",
    )
    ap.add_argument(
        "--reward-window",
        type=int,
        default=100,
        help="(paper-plots) reward moving average window (steps)",
    )
    ap.add_argument(
        "--action-bins",
        type=int,
        default=10,
        help="(paper-plots) action distribution heatmap bins",
    )
    ap.add_argument(
        "--top-actions",
        type=int,
        default=12,
        help="(paper-plots) action heatmap에 표시할 상위 action 개수",
    )
    ap.add_argument(
        "--cellular-var-period-s",
        type=int,
        default=30,
        help="(paper-plots) cellular_var 링크 토글 주기(초, 근사 표시용)",
    )
    ap.add_argument("--discord-webhook", default=None,
                    help="요약 결과를 전송할 Discord webhook URL")
    ap.add_argument("--discord-username", default=None,
                    help="Discord 메시지에 사용할 표시 이름(옵션)")
    ap.add_argument("--discord-mention", action="append", default=[],
                    help="메시지에서 멘션할 Discord 사용자 ID (여러 번 지정 가능)")
    ap.add_argument(
        "--audit",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="quality audit 리포트(quality_audit.json/.md) 생성 (default: disabled)",
    )
    add_logging_cli_args(ap)
    return ap.parse_args()


def main():
    """CLI entry point for offline analysis.

    Args:
        None.

    Returns:
        None.

    Raises:
        SystemExit: If CLI arguments are invalid or processing fails.

    Side Effects:
        - Reads input files and writes analysis artifacts to disk.

    Contract:
        - Requires at least one --input path.

    Failure Modes:
        - Propagates SystemExit for fatal errors.
    """
    args = parse_args()
    setup_logging_from_args(args)
    _clear_plot_manifest()
    out_dir = Path(args.out)
    plot_cfg = PlotConfig(
        dir_name=str(args.plot_dir),
        formats=_parse_plot_formats(str(args.plot_formats)),
        dpi=int(args.plot_dpi),
    )
    baseline_policy = str(args.baseline_policy)

    df = load_events(args.input)
    df = dedup_and_sort(df)
    try:
        decisions = load_decisions(args.input)
    except Exception:
        logger.exception("failed to load decisions logs")
        decisions = pd.DataFrame()

    meta = load_collector_meta(args.input)
    by_run = summarize_by_run(df)
    try:
        qual = compute_seq_aligned_quality_metrics(
            df,
            # KPI quality/coverage are always defined against the periodic baseline
            # (independent of the report's `--baseline-policy` for rate/AoI/MAE tables).
            baseline_policy="periodic",
            tau_ref_policy="fixed_tau",
        )
    except Exception:
        logger.exception("failed to compute seq-aligned quality metrics")
        qual = pd.DataFrame()
    if not qual.empty:
        by_run = by_run.merge(qual, how="left", on=["run_id", "profile", "policy", "sensor"])
    if not meta.empty:
        by_run = by_run.merge(meta, how="left", on=["run_id"])

    arm_dist = pd.DataFrame()
    entropy_win = pd.DataFrame()
    decisions_enriched = pd.DataFrame()
    arm_dist_path: Path | None = None
    entropy_path: Path | None = None
    if not decisions.empty:
        decisions_enriched = enrich_decisions_with_events(decisions, df)
        diag_dec, arm_dist, entropy_win = summarize_decisions_diagnostics_by_run(
            decisions_enriched,
            window_s=60,
        )
        if not diag_dec.empty:
            by_run = by_run.merge(
                diag_dec,
                how="left",
                on=["run_id", "profile", "policy", "sensor"],
            )
    summary = summarize(by_run)
    comparisons = compare_policies(summary, baseline_policy=baseline_policy)

    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        meta = {
            "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "analysis_dir": str(out_dir),
            "inputs": [str(x) for x in args.input],
            "baseline_policy": baseline_policy,
            "flags": {
                "plots": bool(args.plots),
                "paper_plots": bool(args.paper_plots),
                "diagnostic_plots": bool(args.diagnostic_plots),
                "ucb_timeseries": bool(args.ucb_timeseries),
                "pareto_p95": bool(args.pareto_p95),
            },
            "plot_cfg": {
                "dir_name": str(plot_cfg.dir_name),
                "formats": list(plot_cfg.formats),
                "dpi": int(plot_cfg.dpi),
            },
        }
        (out_dir / "analysis_meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("failed to write analysis_meta.json")
    if not arm_dist.empty:
        arm_dist_path = out_dir / "linucb_arm_distribution.csv"
        arm_dist.sort_values(
            ["run_id", "profile", "policy", "sensor", "arm_id"], kind="mergesort"
        ).to_csv(arm_dist_path, index=False)
    if not entropy_win.empty:
        entropy_path = out_dir / "linucb_entropy_60s.csv"
        entropy_win.sort_values(
            ["run_id", "profile", "policy", "sensor", "window_idx"], kind="mergesort"
        ).to_csv(entropy_path, index=False)
    # 저장
    csv_path = out_dir / "metrics_summary.csv"
    summary.to_csv(csv_path, index=False)
    by_run_path = out_dir / "metrics_by_run.csv"
    by_run.to_csv(by_run_path, index=False)
    cmp_path = out_dir / f"metrics_vs_{baseline_policy}.csv"
    comparisons.to_csv(cmp_path, index=False)
    # KPI uses both periodic and fixed_tau baselines regardless of `--baseline-policy`.
    if baseline_policy != "periodic":
        try:
            compare_policies(summary, baseline_policy="periodic").to_csv(
                out_dir / "metrics_vs_periodic.csv",
                index=False,
            )
        except Exception:
            logger.exception("failed to write metrics_vs_periodic.csv")
    if baseline_policy != "fixed_tau":
        try:
            compare_policies(summary, baseline_policy="fixed_tau").to_csv(
                out_dir / "metrics_vs_fixed_tau.csv",
                index=False,
            )
        except Exception:
            logger.exception("failed to write metrics_vs_fixed_tau.csv")

    try:
        kpi, project_pass = compute_final_kpi(summary)
        kpi_path = out_dir / "kpi_final.csv"
        kpi.to_csv(kpi_path, index=False)
        kpi_available = (not summary.empty) and (not kpi.empty)
        verdict = (
            "PASS"
            if project_pass
            else ("FAIL" if kpi_available else ("FAIL" if summary.empty else "SKIP"))
        )
        reason = None
        if verdict == "SKIP":
            reason = "no `adaptive` policy rows found in inputs; KPI is not applicable."
        elif verdict == "FAIL" and summary.empty:
            reason = "no metrics were computed from inputs; KPI could not be evaluated."

        failed_pairs = []
        if kpi_available:
            failed_pairs = [
                {"profile": str(r["profile"]), "sensor": str(r["sensor"])}
                for _, r in kpi.iterrows()
                if str(r.get("overall")) != "PASS"
            ]
        (out_dir / "kpi_verdict.json").write_text(
            json.dumps(
                {
                    "project_verdict": str(verdict),
                    "failed": failed_pairs,
                    **({"reason": str(reason)} if reason else {}),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("failed to write KPI artifacts (kpi_final.csv/kpi_verdict.json)")
    if args.save_parquet:
        try:
            pq_path = out_dir / "metrics_summary.parquet"
            summary.to_parquet(pq_path, index=False)
        except Exception:
            pass

    figures: list[Path] = []
    paper_figures: list[Path] = []
    diag_figures: list[Path] = []
    figures, paper_figures, diag_figures = _generate_plots_impl(
        logger=logger,
        out_dir=out_dir,
        events=df,
        decisions=decisions,
        decisions_enriched=decisions_enriched,
        by_run=by_run,
        summary=summary,
        arm_distribution=arm_dist,
        entropy_windows=entropy_win,
        plot_cfg=plot_cfg,
        plots_enabled=bool(args.plots),
        pareto_p95=bool(args.pareto_p95),
        paper_plots_enabled=bool(args.paper_plots),
        policy_config_path=str(args.policy_config),
        reward_window=int(args.reward_window),
        action_bins=int(args.action_bins),
        top_actions=int(args.top_actions),
        cellular_var_period_s=int(args.cellular_var_period_s),
        diagnostic_plots_enabled=bool(args.diagnostic_plots),
        arm_top_n=int(args.arm_top_n),
        entropy_smooth_window=int(args.entropy_smooth_window),
        ucb_timeseries=bool(args.ucb_timeseries),
        try_make_plots=_try_make_plots,
        try_make_pipeline_plots=_try_make_pipeline_plots,
        try_make_paper_plots=_try_make_paper_plots,
        try_make_diagnostic_plots=_try_make_diagnostic_plots,
    )

    _write_report_md(
        out_dir,
        summary,
        comparisons=comparisons,
        baseline_policy=baseline_policy,
        figures_dir=str(plot_cfg.dir_name),
    )

    if _PLOT_MANIFEST:
        try:
            manifest_path = _write_plot_manifest_impl(
                out_dir,
                dir_name=str(plot_cfg.dir_name),
                formats=plot_cfg.formats,
                dpi=int(plot_cfg.dpi),
            )
            if manifest_path is not None:
                logger.info("saved: %s", manifest_path)
        except Exception:
            logger.exception("failed to write plot_manifest.json")

    if bool(args.audit):
        try:
            from collector.quality_audit import run_quality_audit, write_quality_audit_files

            audit = run_quality_audit(out_dir, figs_dir_name=str(plot_cfg.dir_name))
            qj, qm = write_quality_audit_files(audit, analysis_dir=out_dir)
            logger.info("audit: %s", qj)
            logger.info("audit: %s", qm)
        except Exception:
            logger.exception("failed to write quality audit reports")

    scenarios = summary[["profile", "policy", "sensor"]].drop_duplicates().shape[0]
    logger.info("rows=%s scenarios=%s", len(df), scenarios)
    logger.info("saved: %s", csv_path)
    logger.info("saved: %s", by_run_path)
    logger.info("saved: %s", cmp_path)
    if arm_dist_path is not None:
        logger.info("saved: %s", arm_dist_path)
    if entropy_path is not None:
        logger.info("saved: %s", entropy_path)
    if args.save_parquet:
        logger.info("saved: %s", pq_path)
    figs_root = out_dir / plot_cfg.dir_name
    if figures:
        logger.info("figures: %s files under %s", len(figures), figs_root)
    if paper_figures:
        logger.info("paper figures: %s files under %s", len(paper_figures), figs_root)
    if diag_figures:
        logger.info("diagnostic figures: %s files under %s", len(diag_figures), figs_root)

    if args.discord_webhook:
        mentions = [m.strip() for m in args.discord_mention if m and m.strip()]
        mention_prefix = ""
        allowed_mentions = None
        if mentions:
            mention_prefix = " ".join(f"<@{m}>" for m in mentions) + "\n"
            allowed_mentions = {"parse": [], "users": mentions}
        message = mention_prefix + format_summary_for_discord(summary)
        try:
            send_discord_message(
                args.discord_webhook,
                message,
                username=args.discord_username,
                allowed_mentions=allowed_mentions,
            )
            logger.info("sent Discord notification")
        except DiscordWebhookError as e:
            logger.warning("failed to send Discord notification: %s", e)


if __name__ == "__main__":
    main()





