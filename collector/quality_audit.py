from __future__ import annotations

import argparse
import ast
import json
import logging
import math
import re
import struct
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from common.logging_setup import add_logging_cli_args, setup_logging_from_args

AuditStatus = Literal["PASS", "FAIL", "SKIP"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpectedFigure:
    base_name: str
    formats: tuple[str, ...]
    status: AuditStatus
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FileCheck:
    path: str
    status: AuditStatus
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _slug(x: str) -> str:
    return (
        str(x)
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
        .replace(":", "_")
        .replace("|", "_")
    )


def fig_basename(
    *,
    sensor: str,
    profile: str,
    policy: str,
    metric: str,
    run_id: str | None = None,
) -> str:
    base = f"{_slug(sensor)}_{_slug(profile)}_{_slug(policy)}_{_slug(metric)}"
    if run_id:
        base = f"{base}__{_slug(run_id)}"
    return base


def _discover_figs_dir(analysis_dir: Path, *, preferred: str = "figs") -> Path | None:
    p = analysis_dir / preferred
    if p.is_dir():
        return p
    for child in analysis_dir.iterdir():
        if child.is_dir() and any(child.glob("*.png")):
            return child
    return None


def _infer_plot_formats(figs_dir: Path) -> tuple[str, ...]:
    allowed = {"png", "pdf", "svg"}
    exts: list[str] = []
    for p in figs_dir.iterdir():
        if not p.is_file():
            continue
        ext = p.suffix.lower().lstrip(".")
        if ext in allowed and ext not in exts:
            exts.append(ext)
    return tuple(exts) if exts else ("png",)


def _parse_png_info(path: Path) -> dict[str, Any]:
    """
    Read minimal PNG metadata without PIL:
    - width/height from IHDR
    - effective DPI from pHYs (pixels-per-meter)
    """
    with path.open("rb") as f:
        sig = f.read(8)
        if sig != b"\x89PNG\r\n\x1a\n":
            raise ValueError("not a PNG (bad signature)")

        width: int | None = None
        height: int | None = None
        dpi_x: float | None = None
        dpi_y: float | None = None

        while True:
            len_bytes = f.read(4)
            if not len_bytes:
                break
            (ln,) = struct.unpack(">I", len_bytes)
            ctype = f.read(4)
            data = f.read(ln)
            f.read(4)  # crc

            if ctype == b"IHDR":
                width, height = struct.unpack(">II", data[:8])
            elif ctype == b"pHYs" and len(data) == 9:
                xppm, yppm, unit = struct.unpack(">IIB", data)
                if unit == 1:  # meters
                    dpi_x = float(xppm) * 0.0254
                    dpi_y = float(yppm) * 0.0254
            elif ctype == b"IEND":
                break

    return {"width_px": width, "height_px": height, "dpi_x": dpi_x, "dpi_y": dpi_y}


def _isfinite_series(s: pd.Series) -> np.ndarray:
    v = pd.to_numeric(s, errors="coerce").to_numpy(dtype=np.float64, copy=False)
    return np.isfinite(v)


def _validate_figure_name(
    base_name: str,
    *,
    allowed_sensors: set[str],
    allowed_profiles: set[str],
    allowed_policies: set[str],
) -> tuple[bool, dict[str, Any]]:
    """
    Validate naming rule from `docs/metrics/FIGURE_NAMING.md`:
      {sensor}_{profile}_{policy}_{metric}[__{run_id}]
    """
    slug_sensors = {_slug(s) for s in allowed_sensors}
    slug_profiles = {_slug(s) for s in allowed_profiles}
    slug_policies = {_slug(s) for s in allowed_policies}

    for sensor in slug_sensors:
        for profile in slug_profiles:
            for policy in slug_policies:
                prefix = f"{sensor}_{profile}_{policy}_"
                if not base_name.startswith(prefix):
                    continue
                rest = base_name[len(prefix) :]
                if not rest:
                    return False, {"reason": "missing metric segment"}
                metric, sep, run_id = rest.partition("__")
                if not metric:
                    return False, {"reason": "empty metric"}
                if sep and not run_id:
                    return False, {"reason": "empty run_id suffix"}
                return True, {
                    "sensor": sensor,
                    "profile": profile,
                    "policy": policy,
                    "metric": metric,
                    "run_id": run_id,
                }

    return False, {
        "reason": (
            "does not match {sensor}_{profile}_{policy}_{metric}[__run_id] with allowed values"
        )
    }


def _table_metric_audit(df: pd.DataFrame, *, group_key: str | None = "policy") -> dict[str, Any]:
    """
    NaN/inf coverage audit per numeric column.

    Rule:
    - For each column, evaluate within `group_key` (if present):
        - PASS if all finite in a group
        - SKIP if all non-finite in a group
        - FAIL if mixed finite/non-finite in a group
      Overall status is FAIL if any group FAIL, else PASS if any group PASS, else SKIP.
    - Any +/-inf is always FAIL.
    """
    results: dict[str, Any] = {}
    if df.empty:
        return results

    metric_cols: list[str] = []
    for col in df.columns:
        if col in {"profile", "policy", "sensor", "run_id", "time_base", "__source_file"}:
            continue
        metric_cols.append(col)

    groups: list[tuple[str, pd.DataFrame]]
    if group_key and group_key in df.columns:
        groups = [(str(k), g) for k, g in df.groupby(group_key, sort=False, dropna=False)]
    else:
        groups = [("all", df)]

    for col in sorted(metric_cols):
        by_group: dict[str, Any] = {}
        any_fail = False
        any_pass = False
        any_inf = False

        for gname, g in groups:
            v = pd.to_numeric(g[col], errors="coerce").to_numpy(dtype=np.float64)
            finite = np.isfinite(v)
            inf = np.isinf(v)
            any_inf = any_inf or bool(inf.any())
            n = int(v.size)
            n_finite = int(finite.sum())
            n_nonfinite = n - n_finite

            if n == 0:
                status: AuditStatus = "SKIP"
                reason = "empty"
            elif inf.any():
                status = "FAIL"
                reason = "contains inf"
            elif n_finite == 0:
                status = "SKIP"
                reason = "all NaN"
            elif n_nonfinite == 0:
                status = "PASS"
                reason = "all finite"
            else:
                status = "FAIL"
                reason = "mixed finite/NaN"

            any_fail = any_fail or status == "FAIL"
            any_pass = any_pass or status == "PASS"

            by_group[gname] = {
                "status": status,
                "reason": reason,
                "n": n,
                "finite": n_finite,
                "nonfinite": n_nonfinite,
                "finite_ratio": float(n_finite / max(1, n)),
            }

        if any_inf or any_fail:
            overall: AuditStatus = "FAIL"
        elif any_pass:
            overall = "PASS"
        else:
            overall = "SKIP"

        results[col] = {"status": overall, "by_group": by_group}

    return results


def _scan_print_calls(py_paths: list[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in py_paths:
        try:
            src = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            src = p.read_text(encoding="utf-8", errors="replace")

        try:
            tree = ast.parse(src, filename=str(p))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                out.append(
                    {
                        "path": str(p).replace("\\", "/"),
                        "line": int(getattr(node, "lineno", 0)),
                    }
                )
    return out


def _scan_except_without_logger_exception(py_paths: list[Path]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in py_paths:
        try:
            src = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            src = p.read_text(encoding="utf-8", errors="replace")

        try:
            tree = ast.parse(src, filename=str(p))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue

            has_raise = any(isinstance(n, ast.Raise) for n in ast.walk(node))
            has_exception_log = any(
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "exception"
                for n in ast.walk(node)
            )
            if has_raise or has_exception_log:
                continue

            out.append(
                {
                    "path": str(p).replace("\\", "/"),
                    "line": int(getattr(node, "lineno", 0)),
                    "reason": "except without logger.exception() or re-raise",
                }
            )

    return out


def _is_broad_except(handler: ast.ExceptHandler) -> bool:
    """
    Return True for broad exception handlers that should carry tracebacks:
    - bare `except:`
    - `except Exception:` / `except BaseException:`
    - tuples that include those.
    """

    def _is_broad_type(t: ast.expr | None) -> bool:
        if t is None:
            return True
        if isinstance(t, ast.Name) and t.id in {"Exception", "BaseException"}:
            return True
        if isinstance(t, ast.Tuple):
            return any(_is_broad_type(elt) for elt in t.elts)
        return False

    return _is_broad_type(handler.type)


def _except_handler_has_traceback_log(handler: ast.ExceptHandler) -> bool:
    # `logger.exception(...)` style
    for n in ast.walk(handler):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "exception"
        ):
            return True

    # `logger.warning(..., exc_info=True)` style (traceback, but gated by log level).
    for n in ast.walk(handler):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
            continue
        if n.func.attr not in {"debug", "info", "warning", "error", "critical"}:
            continue
        for kw in n.keywords or []:
            if kw.arg != "exc_info":
                continue
            # Treat any non-False literal / expression as "traceback included".
            if isinstance(kw.value, ast.Constant) and kw.value.value in {False, None}:
                continue
            return True

    return False


def _except_handler_is_trivial(handler: ast.ExceptHandler) -> bool:
    return all(isinstance(s, (ast.Pass, ast.Continue)) for s in handler.body)


def _scan_except_without_traceback(
    py_paths: list[Path],
    *,
    allowlist_by_path: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """
    Scan a small set of operational modules for broad exception handlers that:
    - swallow errors (no re-raise), and
    - do not include a traceback (`logger.exception(...)` or `exc_info=True`).

    This deliberately ignores:
    - narrow exception handlers (e.g., `except OSError:`) which often represent expected control
      flow,
    - pass/continue-only handlers (cleanup best-effort),
    - module-level compatibility fallbacks.
    """
    allowlist_by_path = allowlist_by_path or {}
    failures: list[dict[str, Any]] = []
    skipped = 0

    for p in py_paths:
        if not p.exists():
            continue
        try:
            src = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            src = p.read_text(encoding="utf-8", errors="replace")

        try:
            tree = ast.parse(src, filename=str(p))
        except SyntaxError:
            continue

        allow_funcs = allowlist_by_path.get(str(p).replace("\\", "/"), set())

        class _V(ast.NodeVisitor):
            def __init__(self) -> None:
                self.stack: list[str] = []

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
                self.stack.append(node.name)
                self.generic_visit(node)
                self.stack.pop()

            def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
                nonlocal skipped, failures
                func_name = self.stack[-1] if self.stack else None

                if func_name is None:
                    skipped += 1
                    return

                if not _is_broad_except(node):
                    skipped += 1
                    return

                has_raise = any(isinstance(n, ast.Raise) for n in ast.walk(node))
                has_tb = _except_handler_has_traceback_log(node)
                if has_raise or has_tb:
                    return

                if func_name in allow_funcs:
                    skipped += 1
                    return

                if _except_handler_is_trivial(node):
                    skipped += 1
                    return

                failures.append(
                    {
                        "path": str(p).replace("\\", "/"),
                        "line": int(getattr(node, "lineno", 0)),
                        "function": func_name,
                        "reason": "broad except without traceback logging or re-raise",
                    }
                )

        _V().visit(tree)

    return {"failures": failures, "skipped": int(skipped)}


def _audit_policy_diag_debug_log(path: Path) -> dict[str, Any]:
    required_keys = [
        "run_id",
        "device_id",
        "sensor",
        "seq",
        "arm_id",
        "tau",
        "kbits",
        "safe_arm_forced",
        "forced_reason",
        "exploitation",
        "exploration",
        "score",
        "ucb_alpha",
        "reward_aoi",
        "reward_mae",
        "reward_rate",
        "rate_limit_skips",
        "t_predict_ms",
        "t_decide_ms",
        "t_observe_ms",
        "t_step_ms",
        "cpu_step_ms",
        "maxrss_kb",
    ]

    if not path.exists():
        return {
            "status": "FAIL",
            "reason": "policy debug log source missing",
            "details": {"path": str(path)},
        }

    try:
        txt = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        txt = path.read_text(encoding="utf-8", errors="replace")

    if "policy_diag" not in txt:
        return {
            "status": "FAIL",
            "reason": "missing policy_diag debug log",
            "details": {"required_keys": required_keys},
        }

    missing = [k for k in required_keys if f"{k}=" not in txt]
    if missing:
        return {
            "status": "FAIL",
            "reason": "policy_diag missing required keys",
            "details": {"missing_keys": missing, "required_keys": required_keys},
        }

    return {"status": "PASS", "reason": "ok", "details": {"required_keys": required_keys}}


def run_quality_audit(
    analysis_dir: Path,
    *,
    figs_dir_name: str = "figs",
    min_png_bytes: int = 20_000,
    min_pdf_bytes: int = 2_000,
    min_png_width: int = 1200,
    min_png_height: int = 800,
    require_png_dpi: int = 300,
    require_vector: bool = True,
    code_roots: tuple[str, ...] = ("edge", "collector", "common", "link", "stack"),
) -> dict[str, Any]:
    analysis_dir = Path(analysis_dir)
    figs_dir = _discover_figs_dir(analysis_dir, preferred=figs_dir_name)

    summary_path = analysis_dir / "metrics_summary.csv"
    by_run_path = analysis_dir / "metrics_by_run.csv"
    vs_paths = sorted(analysis_dir.glob("metrics_vs_*.csv"))

    summary = pd.read_csv(summary_path) if summary_path.exists() else pd.DataFrame()
    by_run = pd.read_csv(by_run_path) if by_run_path.exists() else pd.DataFrame()
    arm_dist_path = analysis_dir / "linucb_arm_distribution.csv"
    entropy_path = analysis_dir / "linucb_entropy_60s.csv"
    arm_dist = pd.read_csv(arm_dist_path) if arm_dist_path.exists() else pd.DataFrame()
    entropy = pd.read_csv(entropy_path) if entropy_path.exists() else pd.DataFrame()

    meta_path = analysis_dir / "analysis_meta.json"
    meta: dict[str, Any] = {}
    flags: dict[str, Any] = {}
    plot_cfg_meta: dict[str, Any] = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            flags = meta.get("flags", {}) or {}
            plot_cfg_meta = meta.get("plot_cfg", {}) or {}
        except Exception:
            meta = {}
            flags = {}
            plot_cfg_meta = {}

    plots_enabled = bool(flags.get("plots", True))
    diagnostic_plots_enabled = bool(flags.get("diagnostic_plots", False))
    ucb_timeseries_enabled = bool(flags.get("ucb_timeseries", False))
    pareto_p95_enabled = bool(flags.get("pareto_p95", False))

    figure_files: list[Path] = []
    if figs_dir is not None:
        figure_files = [p for p in figs_dir.iterdir() if p.is_file()]
    meta_formats = [
        str(x).lower()
        for x in (plot_cfg_meta.get("formats", []) if isinstance(plot_cfg_meta, dict) else [])
        if x
    ]
    plot_formats = (
        tuple(meta_formats) if meta_formats else _infer_plot_formats(figs_dir)
    ) if figs_dir is not None else ("png",)
    format_audit = {"status": "PASS", "reason": "ok", "details": {"formats": plot_formats}}
    if not plots_enabled or figs_dir is None:
        format_audit = {
            "status": "SKIP",
            "reason": "plots disabled or figs dir missing",
            "details": {"formats": plot_formats},
        }
    if format_audit.get("status") == "PASS":
        if "png" not in plot_formats:
            format_audit = {
                "status": "FAIL",
                "reason": "PNG format missing",
                "details": {"formats": plot_formats},
            }
        if bool(require_vector) and not any(fmt in {"pdf", "svg"} for fmt in plot_formats):
            format_audit = {
                "status": "FAIL",
                "reason": "vector format missing (pdf/svg)",
                "details": {"formats": plot_formats},
            }

    # Allowed naming values (data-driven)
    sensors: set[str] = {"all"}
    profiles: set[str] = {"all"}
    policies: set[str] = {"compare"}
    run_ids: set[str] = set()

    for df in (summary, by_run):
        if df.empty:
            continue
        if "sensor" in df.columns:
            sensors |= {str(x) for x in df["sensor"].dropna().astype("string").unique().tolist()}
        if "profile" in df.columns:
            profiles |= {str(x) for x in df["profile"].dropna().astype("string").unique().tolist()}
        if "policy" in df.columns:
            policies |= {str(x) for x in df["policy"].dropna().astype("string").unique().tolist()}
        if "run_id" in df.columns:
            run_ids |= {str(x) for x in df["run_id"].dropna().astype("string").unique().tolist()}

    # ---------------- Expected figure list (data-driven) ----------------
    expected: list[ExpectedFigure] = []

    if plots_enabled and not summary.empty and {"profile", "sensor", "policy"}.issubset(
        summary.columns
    ):
        for (prof, sensor), g in summary.groupby(["profile", "sensor"], sort=False, dropna=False):
            prof_s = str(prof)
            sensor_s = str(sensor)

            def _expect_compare_bar(metric_col: str, metric_name: str) -> None:
                base = fig_basename(
                    sensor=sensor_s,
                    profile=prof_s,
                    policy="compare",
                    metric=metric_name,
                )
                if metric_col not in g.columns:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="SKIP",
                            reason=f"column missing: {metric_col}",
                        )
                    )
                    return
                if not _isfinite_series(g[metric_col]).any():
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="SKIP",
                            reason=f"no finite values in {metric_col}",
                        )
                    )
                    return
                expected.append(
                    ExpectedFigure(
                        base_name=base,
                        formats=plot_formats,
                        status="PASS",
                        reason="expected (data present)",
                        details={"driver_col": metric_col},
                    )
                )

            _expect_compare_bar("rate_Bps", "rate_bar")
            _expect_compare_bar("aoi_mean_ms", "aoi_mean_bar")
            _expect_compare_bar("aoi_p95_ms", "aoi_p95_bar")
            _expect_compare_bar("mae_event_mean", "mae_mean_bar")
            _expect_compare_bar("mae_event_p95", "mae_p95_bar")
            _expect_compare_bar("kbits_mean", "kbits_mean_bar")

            # Reward component breakdown (only if components exist)
            comp_cols = [
                "linucb_reward_aoi_mean",
                "linucb_reward_mae_mean",
                "linucb_reward_rate_mean",
            ]
            base = fig_basename(
                sensor=sensor_s,
                profile=prof_s,
                policy="compare",
                metric="reward_components_bar",
            )
            comp_present = set(comp_cols).issubset(g.columns)
            comp_any_finite = False
            if comp_present:
                comp_any_finite = any(_isfinite_series(g[c]).any() for c in comp_cols)
            if comp_present and comp_any_finite:
                expected.append(
                    ExpectedFigure(
                        base_name=base,
                        formats=plot_formats,
                        status="PASS",
                        reason="expected (reward components present)",
                        details={"driver_cols": comp_cols},
                    )
                )
            else:
                expected.append(
                    ExpectedFigure(
                        base_name=base,
                        formats=plot_formats,
                        status="SKIP",
                        reason="reward components missing/invalid",
                        details={"driver_cols": comp_cols},
                    )
                )

            # Pareto: Rate vs AoI mean
            base = fig_basename(
                sensor=sensor_s,
                profile=prof_s,
                policy="compare",
                metric="pareto_rate_vs_aoi_mean",
            )
            if {"rate_Bps", "aoi_mean_ms"}.issubset(g.columns) and (
                _isfinite_series(g["rate_Bps"]).any() and _isfinite_series(g["aoi_mean_ms"]).any()
            ):
                expected.append(
                    ExpectedFigure(
                        base_name=base,
                        formats=plot_formats,
                        status="PASS",
                        reason="expected (data present)",
                        details={"driver_cols": ["rate_Bps", "aoi_mean_ms"]},
                    )
                )
            else:
                expected.append(
                    ExpectedFigure(
                        base_name=base,
                        formats=plot_formats,
                        status="SKIP",
                        reason="missing/invalid Rate or AoI mean",
                        details={"driver_cols": ["rate_Bps", "aoi_mean_ms"]},
                    )
                )
            if bool(pareto_p95_enabled):
                base = fig_basename(
                    sensor=sensor_s,
                    profile=prof_s,
                    policy="compare",
                    metric="pareto_rate_vs_aoi_p95",
                )
                if {"rate_Bps", "aoi_p95_ms"}.issubset(g.columns) and (
                    _isfinite_series(g["rate_Bps"]).any()
                    and _isfinite_series(g["aoi_p95_ms"]).any()
                ):
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="PASS",
                            reason="expected (data present)",
                            details={"driver_cols": ["rate_Bps", "aoi_p95_ms"]},
                        )
                    )
                else:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="SKIP",
                            reason="missing/invalid Rate or AoI p95",
                            details={"driver_cols": ["rate_Bps", "aoi_p95_ms"]},
                        )
                    )

            # Pipeline: latency distribution (boxplot)
            base = fig_basename(
                sensor=sensor_s,
                profile=prof_s,
                policy="compare",
                metric="rx_delay_box",
            )
            if {"rx_delay_p50_ms", "rx_delay_p95_ms"}.issubset(g.columns) and (
                _isfinite_series(g["rx_delay_p50_ms"]).any()
                or _isfinite_series(g["rx_delay_p95_ms"]).any()
            ):
                expected.append(
                    ExpectedFigure(
                        base_name=base,
                        formats=plot_formats,
                        status="PASS",
                        reason="expected (rx_delay present)",
                        details={"driver_cols": ["rx_delay_p50_ms", "rx_delay_p95_ms"]},
                    )
                )
            else:
                expected.append(
                    ExpectedFigure(
                        base_name=base,
                        formats=plot_formats,
                        status="SKIP",
                        reason="rx_delay metrics missing/invalid",
                        details={"driver_cols": ["rx_delay_p50_ms", "rx_delay_p95_ms"]},
                    )
                )

    # Pipeline: outbox backlog time-series is per-run
    if plots_enabled and not by_run.empty and {"run_id", "profile", "policy"}.issubset(
        by_run.columns
    ):
        need_outbox = {"outbox_pending_max", "outbox_pending_auc_s", "outbox_pending_recovery_s"}
        if need_outbox.issubset(by_run.columns):
            for (run_id, prof, pol), g in by_run.groupby(
                ["run_id", "profile", "policy"], sort=False, dropna=False
            ):
                base = fig_basename(
                    sensor="all",
                    profile=str(prof),
                    policy=str(pol),
                    metric="outbox_pending_ts",
                    run_id=str(run_id),
                )
                any_finite = False
                for col in sorted(need_outbox):
                    any_finite = any_finite or bool(_isfinite_series(g[col]).any())
                if not any_finite:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="SKIP",
                            reason="outbox metrics present but all non-finite",
                        )
                    )
                    continue
                expected.append(
                    ExpectedFigure(
                        base_name=base,
                        formats=plot_formats,
                        status="PASS",
                        reason="expected (outbox metrics present)",
                        details={
                            "run_id": str(run_id),
                            "profile": str(prof),
                            "policy": str(pol),
                        },
                    )
                )

    # Pipeline: duplicate bytes ratio (bar, compare)
    if plots_enabled and not by_run.empty and {"profile", "policy", "dup_bytes_ratio"}.issubset(
        by_run.columns
    ):
        for prof, g in by_run.groupby("profile", sort=False, dropna=False):
            base = fig_basename(
                sensor="all",
                profile=str(prof),
                policy="compare",
                metric="dup_bytes_ratio",
            )
            vals = pd.to_numeric(g["dup_bytes_ratio"], errors="coerce")
            if vals.notna().any() and np.isfinite(vals.to_numpy(dtype=np.float64)).any():
                expected.append(
                    ExpectedFigure(
                        base_name=base,
                        formats=plot_formats,
                        status="PASS",
                        reason="expected (dup_bytes_ratio present)",
                        details={"driver_col": "dup_bytes_ratio"},
                    )
                )
            else:
                expected.append(
                    ExpectedFigure(
                        base_name=base,
                        formats=plot_formats,
                        status="SKIP",
                        reason="dup_bytes_ratio missing/invalid",
                        details={"driver_col": "dup_bytes_ratio"},
                    )
                )

    # Diagnostic plots (adaptive only)
    if bool(diagnostic_plots_enabled):
        # Arm distribution (per run)
        need_arm = {"run_id", "profile", "sensor", "arm_id", "frac"}
        if not arm_dist.empty and need_arm.issubset(arm_dist.columns):
            ad = arm_dist.copy()
            if "policy" in ad.columns:
                ad = ad[ad["policy"].astype("string") == "adaptive"]
            for (run_id, prof, sensor), g in ad.groupby(
                ["run_id", "profile", "sensor"], sort=False, dropna=False
            ):
                base = fig_basename(
                    sensor=str(sensor),
                    profile=str(prof),
                    policy="adaptive",
                    metric="arm_dist",
                    run_id=str(run_id),
                )
                if _isfinite_series(g["frac"]).any():
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="PASS",
                            reason="expected (arm distribution present)",
                            details={"driver_col": "frac"},
                        )
                    )
                else:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="SKIP",
                            reason="arm distribution missing/invalid",
                            details={"driver_col": "frac"},
                        )
                    )

        # Entropy time-series (per run + window)
        need_entropy = {"run_id", "profile", "sensor", "window_idx", "entropy_log2"}
        if not entropy.empty and need_entropy.issubset(entropy.columns):
            ew = entropy.copy()
            if "policy" in ew.columns:
                ew = ew[ew["policy"].astype("string") == "adaptive"]
            for (run_id, prof, sensor), g in ew.groupby(
                ["run_id", "profile", "sensor"], sort=False, dropna=False
            ):
                win_s = 60
                if "window_s" in g.columns and g["window_s"].notna().any():
                    try:
                        win_s_raw = pd.to_numeric(g["window_s"], errors="coerce").dropna()
                        win_s = int(float(win_s_raw.iloc[0]))
                    except Exception:
                        win_s = 60
                base = fig_basename(
                    sensor=str(sensor),
                    profile=str(prof),
                    policy="adaptive",
                    metric=f"entropy_{win_s}s",
                    run_id=str(run_id),
                )
                if _isfinite_series(g["entropy_log2"]).any():
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="PASS",
                            reason="expected (entropy present)",
                            details={"driver_col": "entropy_log2"},
                        )
                    )
                else:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="SKIP",
                            reason="entropy missing/invalid",
                            details={"driver_col": "entropy_log2"},
                        )
                    )

        # Safe-arm forced reasons (stacked bar, per sensor)
        need_safe = {
            "policy",
            "sensor",
            "linucb_safe_forced_rate",
            "linucb_forced_reason_aoi_limit_rate",
            "linucb_forced_reason_mae_limit_rate",
            "linucb_forced_reason_both_rate",
        }
        if not summary.empty and need_safe.issubset(summary.columns):
            ss = summary.copy()
            ss = ss[ss["policy"].astype("string") == "adaptive"]
            for sensor, g in ss.groupby("sensor", sort=False, dropna=False):
                base = fig_basename(
                    sensor=str(sensor),
                    profile="all",
                    policy="adaptive",
                    metric="safe_forced_reasons",
                )
                if _isfinite_series(g["linucb_safe_forced_rate"]).any():
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="PASS",
                            reason="expected (safe forced present)",
                            details={"driver_col": "linucb_safe_forced_rate"},
                        )
                    )
                else:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="SKIP",
                            reason="safe forced missing/invalid",
                            details={"driver_col": "linucb_safe_forced_rate"},
                        )
                    )

        # Switch rate (per sensor)
        need_switch = {"policy", "sensor", "linucb_switch_rate"}
        if not summary.empty and need_switch.issubset(summary.columns):
            ss = summary.copy()
            ss = ss[ss["policy"].astype("string") == "adaptive"]
            for sensor, g in ss.groupby("sensor", sort=False, dropna=False):
                base = fig_basename(
                    sensor=str(sensor), profile="all", policy="adaptive", metric="switch_rate"
                )
                if _isfinite_series(g["linucb_switch_rate"]).any():
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="PASS",
                            reason="expected (switch rate present)",
                            details={"driver_col": "linucb_switch_rate"},
                        )
                    )
                else:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="SKIP",
                            reason="switch rate missing/invalid",
                            details={"driver_col": "linucb_switch_rate"},
                        )
                    )

        # Rate-limit skips (per sensor; only when >0)
        skips_col = "linucb_rate_limit_skips_per_decision"
        if not summary.empty and {"policy", "sensor", skips_col}.issubset(summary.columns):
            ss = summary.copy()
            ss = ss[ss["policy"].astype("string") == "adaptive"]
            for sensor, g in ss.groupby("sensor", sort=False, dropna=False):
                base = fig_basename(
                    sensor=str(sensor),
                    profile="all",
                    policy="adaptive",
                    metric="rate_limit_skips_per_decision",
                )
                vals = pd.to_numeric(g[skips_col], errors="coerce").fillna(0.0)
                if float(vals.max()) > 0.0:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="PASS",
                            reason="expected (rate-limit skips present)",
                            details={"driver_col": skips_col},
                        )
                    )
                else:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="SKIP",
                            reason="rate-limit skips all zero",
                            details={"driver_col": skips_col},
                        )
                    )

        # UCB decomposition (per profile/sensor)
        need_ucb = {
            "policy",
            "sensor",
            "profile",
            "linucb_ucb_exploitation_mean",
            "linucb_ucb_exploration_mean",
            "linucb_ucb_score_mean",
            "linucb_ucb_uncertainty_mean",
        }
        if not summary.empty and need_ucb.issubset(summary.columns):
            ss = summary.copy()
            ss = ss[ss["policy"].astype("string") == "adaptive"]
            for (prof, sensor), g in ss.groupby(["profile", "sensor"], sort=False, dropna=False):
                base = fig_basename(
                    sensor=str(sensor),
                    profile=str(prof),
                    policy="adaptive",
                    metric="ucb_decomposition",
                )
                any_finite = any(
                    _isfinite_series(g[c]).any()
                    for c in (
                        "linucb_ucb_exploitation_mean",
                        "linucb_ucb_exploration_mean",
                        "linucb_ucb_score_mean",
                        "linucb_ucb_uncertainty_mean",
                    )
                )
                if any_finite:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="PASS",
                            reason="expected (UCB terms present)",
                            details={"driver_cols": sorted(list(need_ucb))},
                        )
                    )
                else:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="SKIP",
                            reason="UCB terms missing/invalid",
                            details={"driver_cols": sorted(list(need_ucb))},
                        )
                    )

        # Event reasons (per sensor)
        need_reasons = {
            "policy",
            "sensor",
            "profile",
            "event_reason_threshold_count",
            "event_reason_heartbeat_count",
            "linucb_rate_limit_skips_total",
        }
        if not by_run.empty and need_reasons.issubset(by_run.columns):
            br = by_run.copy()
            br = br[br["policy"].astype("string") == "adaptive"]
            for sensor, g in br.groupby("sensor", sort=False, dropna=False):
                base = fig_basename(
                    sensor=str(sensor),
                    profile="all",
                    policy="adaptive",
                    metric="event_reasons",
                )
                thr = pd.to_numeric(g["event_reason_threshold_count"], errors="coerce")
                hb = pd.to_numeric(g["event_reason_heartbeat_count"], errors="coerce")
                sk = pd.to_numeric(g["linucb_rate_limit_skips_total"], errors="coerce")
                total = (thr + hb + sk).replace([np.inf, -np.inf], np.nan)
                if total.notna().any() and float(total.max(skipna=True)) > 0.0:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="PASS",
                            reason="expected (event reasons present)",
                            details={"driver_cols": sorted(list(need_reasons))},
                        )
                    )
                else:
                    expected.append(
                        ExpectedFigure(
                            base_name=base,
                            formats=plot_formats,
                            status="SKIP",
                            reason="event reasons missing/invalid",
                            details={"driver_cols": sorted(list(need_reasons))},
                        )
                    )

        # UCB time-series (per run) if enabled
        if bool(ucb_timeseries_enabled) and not by_run.empty and {
            "run_id",
            "profile",
            "sensor",
            "policy",
            "linucb_n_decisions",
        }.issubset(by_run.columns):
            br = by_run.copy()
            br = br[br["policy"].astype("string") == "adaptive"]
            for _, r in br.iterrows():
                n_dec = float(pd.to_numeric(r.get("linucb_n_decisions"), errors="coerce"))
                if not math.isfinite(n_dec) or n_dec <= 0:
                    continue
                base = fig_basename(
                    sensor=str(r.get("sensor")),
                    profile=str(r.get("profile")),
                    policy="adaptive",
                    metric="ucb_terms_ts",
                    run_id=str(r.get("run_id")),
                )
                expected.append(
                    ExpectedFigure(
                        base_name=base,
                        formats=plot_formats,
                        status="PASS",
                        reason="expected (ucb_timeseries enabled)",
                        details={"driver_col": "linucb_n_decisions"},
                    )
                )

    # ---------------- Evaluate expected vs actual ----------------
    expected_checks: list[dict[str, Any]] = []
    missing_files: list[str] = []
    present_files: set[str] = {p.name for p in figure_files}

    for exp in expected:
        if exp.status != "PASS":
            expected_checks.append(asdict(exp))
            continue

        needed = [f"{exp.base_name}.{fmt}" for fmt in exp.formats]
        missing = [n for n in needed if n not in present_files]
        if not missing:
            expected_checks.append(asdict(exp))
            continue

        expected_checks.append(
            asdict(
                ExpectedFigure(
                    base_name=exp.base_name,
                    formats=exp.formats,
                    status="FAIL",
                    reason="missing expected figure files",
                    details={"missing": missing},
                )
            )
        )
        missing_files.extend(missing)

    # ---------------- Per-file checks ----------------
    file_checks: list[FileCheck] = []
    naming_violations: list[str] = []
    png_quality_fails: list[str] = []
    tiny_files: list[str] = []
    small_png_dims: list[str] = []

    for p in figure_files:
        ext = p.suffix.lower().lstrip(".")
        base = p.stem

        ok_name, name_info = _validate_figure_name(
            base,
            allowed_sensors=sensors,
            allowed_profiles=profiles,
            allowed_policies=policies,
        )
        if not ok_name:
            naming_violations.append(p.name)
            file_checks.append(
                FileCheck(
                    path=str(p).replace("\\", "/"),
                    status="FAIL",
                    reason="figure naming violation",
                    details={
                        "rule": "{sensor}_{profile}_{policy}_{metric}[__run_id]",
                        **name_info,
                    },
                )
            )
            continue

        size = p.stat().st_size
        if ext == "png" and size < min_png_bytes:
            tiny_files.append(p.name)
            file_checks.append(
                FileCheck(
                    path=str(p).replace("\\", "/"),
                    status="FAIL",
                    reason=f"PNG too small (<{min_png_bytes} bytes)",
                    details={"size_bytes": size},
                )
            )
            continue
        if ext == "pdf" and size < min_pdf_bytes:
            tiny_files.append(p.name)
            file_checks.append(
                FileCheck(
                    path=str(p).replace("\\", "/"),
                    status="FAIL",
                    reason=f"PDF too small (<{min_pdf_bytes} bytes)",
                    details={"size_bytes": size},
                )
            )
            continue

        if ext != "png":
            file_checks.append(
                FileCheck(
                    path=str(p).replace("\\", "/"),
                    status="PASS",
                    reason="ok",
                    details={"size_bytes": size, "ext": ext},
                )
            )
            continue

        try:
            info = _parse_png_info(p)
        except Exception as e:
            png_quality_fails.append(p.name)
            file_checks.append(
                FileCheck(
                    path=str(p).replace("\\", "/"),
                    status="FAIL",
                    reason="failed to parse PNG metadata",
                    details={"error": str(e)},
                )
            )
            continue

        width_px = info.get("width_px")
        height_px = info.get("height_px")
        if (
            isinstance(width_px, int)
            and isinstance(height_px, int)
            and (width_px < min_png_width or height_px < min_png_height)
        ):
            small_png_dims.append(p.name)
            file_checks.append(
                FileCheck(
                    path=str(p).replace("\\", "/"),
                    status="FAIL",
                    reason="PNG dimensions too small",
                    details={
                        "width_px": width_px,
                        "height_px": height_px,
                        "min_width": min_png_width,
                        "min_height": min_png_height,
                    },
                )
            )
            continue

        dpi_x = info.get("dpi_x")
        dpi_y = info.get("dpi_y")
        if dpi_x is None or dpi_y is None:
            png_quality_fails.append(p.name)
            file_checks.append(
                FileCheck(
                    path=str(p).replace("\\", "/"),
                    status="FAIL",
                    reason="PNG missing pHYs DPI metadata",
                    details=info,
                )
            )
            continue

        dpi_eff = float(min(float(dpi_x), float(dpi_y)))
        if dpi_eff + 1e-3 < float(require_png_dpi):
            png_quality_fails.append(p.name)
            file_checks.append(
                FileCheck(
                    path=str(p).replace("\\", "/"),
                    status="FAIL",
                    reason=f"effective DPI < {require_png_dpi}",
                    details={"dpi_effective": dpi_eff, **info},
                )
            )
            continue

        file_checks.append(
            FileCheck(
                path=str(p).replace("\\", "/"),
                status="PASS",
                reason="ok",
                details={
                    "size_bytes": size,
                    "dpi_effective": dpi_eff,
                    "width_px": info.get("width_px"),
                    "height_px": info.get("height_px"),
                },
            )
        )

    # ---------------- Label checks (plot_manifest.json) ----------------
    label_checks: list[dict[str, Any]] = []
    manifest_path = analysis_dir / "plot_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for fig in manifest.get("figures", []) or []:
                base_name = str(fig.get("base_name", ""))
                axes = fig.get("axes", []) or []
                axes_nocb = [
                    a
                    for a in axes
                    if "colorbar" not in str(a.get("ax_label", "")).lower()
                    and str(a.get("ax_label", "")).strip() != "<colorbar>"
                ]
                has_xlabel = any(str(a.get("xlabel", "")).strip() for a in axes_nocb)
                has_ylabel = any(str(a.get("ylabel", "")).strip() for a in axes_nocb)
                if has_xlabel and has_ylabel:
                    label_checks.append(
                        {
                            "base_name": base_name,
                            "status": "PASS",
                            "reason": "ok",
                            "details": {"axes_checked": len(axes_nocb)},
                        }
                    )
                else:
                    missing = []
                    if not has_xlabel:
                        missing.append("xlabel")
                    if not has_ylabel:
                        missing.append("ylabel")
                    label_checks.append(
                        {
                            "base_name": base_name,
                            "status": "FAIL",
                            "reason": "missing axis labels",
                            "details": {"missing": missing, "axes_checked": len(axes_nocb)},
                        }
                    )
        except Exception as e:
            label_checks.append(
                {
                    "base_name": "",
                    "status": "FAIL",
                    "reason": "failed to parse plot_manifest.json",
                    "details": {"error": str(e)},
                }
            )

    # ---------------- Table checks ----------------
    table_checks: dict[str, Any] = {}
    if summary_path.exists():
        table_checks["metrics_summary.csv"] = _table_metric_audit(summary)
    if by_run_path.exists():
        table_checks["metrics_by_run.csv"] = _table_metric_audit(by_run)
    for vp in vs_paths:
        try:
            vdf = pd.read_csv(vp)
        except Exception:
            continue
        table_checks[vp.name] = _table_metric_audit(vdf)

    # ---------------- Logging/codebase checks ----------------
    py_paths: list[Path] = []
    for root in code_roots:
        rp = Path(root)
        if not rp.exists():
            continue
        py_paths.extend([p for p in rp.rglob("*.py") if p.is_file()])

    print_calls = _scan_print_calls(py_paths)
    exception_targets = [
        Path("edge/edge_daemon.py"),
        Path("edge/uploader/mqtt_publisher.py"),
        Path("collector/collector.py"),
        Path("link/shaper/tc_profiles.py"),
        Path("stack/pi_stack.py"),
    ]
    allowlist_by_path = {
        "edge/edge_daemon.py": {"_hb_none", "_seed_everything"},
        "link/shaper/tc_profiles.py": {"_require_root"},
        "stack/pi_stack.py": {"run", "_install_signals", "_terminate_all"},
    }
    exception_audit = _scan_except_without_traceback(
        exception_targets,
        allowlist_by_path=allowlist_by_path,
    )
    diag_log_audit = _audit_policy_diag_debug_log(Path("edge/edge_daemon.py"))

    logging_setup = Path("common/logging_setup.py")
    logging_setup_ok = False
    ts_format_ok = False
    if logging_setup.exists():
        logging_setup_ok = True
        txt = logging_setup.read_text(encoding="utf-8", errors="replace")
        ts_format_ok = bool(re.search(r"%\\(asctime\\)s|asctime", txt))

    expected_counts = Counter([x["status"] for x in expected_checks])
    file_counts = Counter([x.status for x in file_checks])
    label_counts = Counter([x.get("status", "SKIP") for x in label_checks])

    def _count_table_status(tbl: dict[str, Any]) -> Counter:
        c = Counter()
        for _col, d in tbl.items():
            c[str(d.get("status", "SKIP"))] += 1
        return c

    table_counts = {name: _count_table_status(tbl) for name, tbl in table_checks.items()}

    logging_counts = Counter()
    logging_counts["FAIL"] += 1 if print_calls else 0
    logging_counts["PASS"] += 0 if print_calls else 1
    logging_counts["FAIL"] += 1 if not (logging_setup_ok and ts_format_ok) else 0
    logging_counts["PASS"] += 1 if (logging_setup_ok and ts_format_ok) else 0
    exc_failures = exception_audit.get("failures") or []
    logging_counts["FAIL"] += 1 if exc_failures else 0
    logging_counts["PASS"] += 0 if exc_failures else 1
    diag_ok = str(diag_log_audit.get("status")) == "PASS"
    logging_counts["FAIL"] += 0 if diag_ok else 1
    logging_counts["PASS"] += 1 if diag_ok else 0

    return {
        "generated_at": _utc_now_iso(),
        "analysis_dir": str(analysis_dir).replace("\\", "/"),
        "figs_dir": str(figs_dir).replace("\\", "/") if figs_dir else None,
        "plot_formats_inferred": plot_formats,
        "plot_flags": {
            "plots_enabled": bool(plots_enabled),
            "diagnostic_plots_enabled": bool(diagnostic_plots_enabled),
            "ucb_timeseries_enabled": bool(ucb_timeseries_enabled),
            "pareto_p95_enabled": bool(pareto_p95_enabled),
        },
        "format_audit": format_audit,
        "visualization": {
            "expected_figures": expected_checks,
            "expected_status_counts": dict(expected_counts),
            "missing_expected_files": sorted(set(missing_files)),
            "file_checks": [asdict(x) for x in file_checks],
            "file_status_counts": dict(file_counts),
            "label_checks": label_checks,
            "label_status_counts": dict(label_counts),
            "naming_violations": sorted(set(naming_violations)),
            "png_quality_fails": sorted(set(png_quality_fails)),
            "tiny_files": sorted(set(tiny_files)),
            "small_png_dims": sorted(set(small_png_dims)),
        },
        "tables": {
            "paths": {
                "metrics_summary.csv": str(summary_path).replace("\\", "/")
                if summary_path.exists()
                else None,
                "metrics_by_run.csv": str(by_run_path).replace("\\", "/")
                if by_run_path.exists()
                else None,
                "metrics_vs": [str(p).replace("\\", "/") for p in vs_paths],
            },
            "metric_coverage": table_checks,
            "status_counts": {k: dict(v) for k, v in table_counts.items()},
        },
        "logging": {
            "print_calls": print_calls,
            "exception_traceback_audit": exception_audit,
            "logging_setup_present": logging_setup_ok,
            "timestamp_format_detected": ts_format_ok,
            "policy_diag_debug_log_audit": diag_log_audit,
            "status_counts": dict(logging_counts),
        },
    }


def _status_counts(items: list[dict[str, Any]]) -> Counter:
    return Counter([str(x.get("status", "SKIP")) for x in items])


def write_quality_audit_files(report: dict[str, Any], *, analysis_dir: Path) -> tuple[Path, Path]:
    analysis_dir = Path(analysis_dir)
    json_path = analysis_dir / "quality_audit.json"
    md_path = analysis_dir / "quality_audit.md"

    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    vis = report.get("visualization", {}) or {}
    exp = vis.get("expected_figures", []) or []
    exp_counts = _status_counts(exp)
    file_checks = vis.get("file_checks", []) or []
    file_counts = _status_counts(file_checks)
    label_checks = vis.get("label_checks", []) or []
    label_counts = _status_counts(label_checks)

    missing = vis.get("missing_expected_files", []) or []
    naming = vis.get("naming_violations", []) or []
    png_fails = vis.get("png_quality_fails", []) or []
    tiny = vis.get("tiny_files", []) or []
    small_dims = vis.get("small_png_dims", []) or []

    format_audit = report.get("format_audit", {}) or {}
    plot_flags = report.get("plot_flags", {}) or {}
    log = report.get("logging", {}) or {}
    print_calls = log.get("print_calls", []) or []
    exc_audit = log.get("exception_traceback_audit", {}) or {}
    exc_fail = exc_audit.get("failures", []) or []
    exc_skipped = int(exc_audit.get("skipped") or 0)
    diag_audit = log.get("policy_diag_debug_log_audit", {}) or {}

    lines: list[str] = []
    lines.append("# Quality audit report")
    lines.append("")
    lines.append(f"- Generated at: `{report.get('generated_at')}`")
    lines.append(f"- Analysis dir: `{report.get('analysis_dir')}`")
    lines.append(f"- Figs dir: `{report.get('figs_dir')}`")
    formats_str = ",".join(report.get("plot_formats_inferred") or [])
    lines.append(f"- Inferred plot formats: `{formats_str}`")
    if plot_flags:
        lines.append(
            "- Plot flags: "
            f"plots={plot_flags.get('plots_enabled')} "
            f"diagnostic={plot_flags.get('diagnostic_plots_enabled')} "
            f"ucb_timeseries={plot_flags.get('ucb_timeseries_enabled')} "
            f"pareto_p95={plot_flags.get('pareto_p95_enabled')}"
        )
    lines.append("")

    lines.append("## Summary")
    lines.append(
        f"- Visualization (expected): "
        f"PASS {exp_counts.get('PASS', 0)} / "
        f"FAIL {exp_counts.get('FAIL', 0)} / "
        f"SKIP {exp_counts.get('SKIP', 0)}"
    )
    lines.append(
        f"- Visualization (files): "
        f"PASS {file_counts.get('PASS', 0)} / "
        f"FAIL {file_counts.get('FAIL', 0)} / "
        f"SKIP {file_counts.get('SKIP', 0)}"
    )
    if label_checks:
        lines.append(
            f"- Visualization (labels): PASS {label_counts.get('PASS', 0)} / "
            f"FAIL {label_counts.get('FAIL', 0)} / SKIP {label_counts.get('SKIP', 0)}"
        )
    if format_audit:
        lines.append(
            f"- Formats: {format_audit.get('status')} "
            f"({format_audit.get('reason')})"
        )
    lines.append(
        f"- Logging: PASS {log.get('status_counts', {}).get('PASS', 0)} / "
        f"FAIL {log.get('status_counts', {}).get('FAIL', 0)}"
    )
    lines.append("")

    if missing:
        lines.append("## Missing expected figures (FAIL)")
        for m in missing:
            lines.append(f"- `{m}`")
        lines.append("")

    if naming:
        lines.append("## Figure naming violations (FAIL)")
        lines.append("- Rule: `{sensor}_{profile}_{policy}_{metric}[__run_id].{ext}`")
        for n in naming:
            lines.append(f"- `{n}`")
        lines.append("")

    if png_fails:
        lines.append("## PNG quality failures (FAIL)")
        for n in png_fails:
            lines.append(f"- `{n}`")
        lines.append("")

    label_fails = [x for x in label_checks if str(x.get("status")) == "FAIL"]
    if label_fails:
        lines.append("## Missing axis labels (FAIL)")
        for it in label_fails[:50]:
            base = it.get("base_name") or ""
            details = it.get("details") or {}
            miss = details.get("missing") if isinstance(details, dict) else None
            if miss:
                lines.append(f"- `{base}` missing={miss}")
            else:
                lines.append(f"- `{base}`")
        lines.append("")

    if tiny:
        lines.append("## Tiny figure files (FAIL)")
        for n in tiny:
            lines.append(f"- `{n}`")
        lines.append("")

    if small_dims:
        lines.append("## Small PNG dimensions (FAIL)")
        for n in small_dims:
            lines.append(f"- `{n}`")
        lines.append("")

    lines.append("## Table metric coverage (NaN/inf)")
    tables = report.get("tables", {}).get("metric_coverage", {}) or {}
    for tname, cols in tables.items():
        counts = Counter()
        for _, d in (cols or {}).items():
            counts[str(d.get("status", "SKIP"))] += 1
        lines.append(
            f"- `{tname}`: PASS {counts.get('PASS', 0)} / FAIL {counts.get('FAIL', 0)} / "
            f"SKIP {counts.get('SKIP', 0)}"
        )
    lines.append("")

    lines.append("## Logging/codebase checks")
    if print_calls:
        lines.append(f"- print(): FAIL ({len(print_calls)} call sites; first 20 shown)")
        for it in print_calls[:20]:
            lines.append(f"  - `{it.get('path')}:{it.get('line')}`")
    else:
        lines.append("- print(): PASS (no call sites found)")

    if exc_fail:
        lines.append(
            f"- broad except without traceback logging (targets): FAIL "
            f"({len(exc_fail)} handlers; first 20 shown)"
        )
        for it in exc_fail[:20]:
            func = it.get("function")
            suffix = f" func={func}" if func else ""
            lines.append(f"  - `{it.get('path')}:{it.get('line')}`{suffix}")
    else:
        lines.append(
            f"- broad except without traceback logging (targets): PASS (skipped {exc_skipped})"
        )

    diag_status = str(diag_audit.get("status") or "FAIL")
    if diag_status == "PASS":
        lines.append("- policy diagnostics DEBUG log keys: PASS")
    else:
        details = diag_audit.get("details") or {}
        missing_keys = details.get("missing_keys") if isinstance(details, dict) else None
        if missing_keys:
            lines.append(f"- policy diagnostics DEBUG log keys: FAIL missing={missing_keys}")
        else:
            lines.append("- policy diagnostics DEBUG log keys: FAIL")

    setup_status = "PASS" if log.get("logging_setup_present") else "FAIL"
    ts_status = "PASS" if log.get("timestamp_format_detected") else "FAIL"
    lines.append(f"- `common/logging_setup.py` present: {setup_status}")
    lines.append(f"- timestamp in formatter detected: {ts_status}")
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Audit analysis outputs for paper-ready quality.")
    ap.add_argument(
        "--analysis-dir",
        required=True,
        help="collector.analyze output directory (contains metrics_*.csv and figs/)",
    )
    ap.add_argument(
        "--figs-dir-name",
        default="figs",
        help="figures subdirectory name (default: figs)",
    )
    ap.add_argument(
        "--min-png-bytes",
        type=int,
        default=20_000,
        help="minimum PNG file size",
    )
    ap.add_argument(
        "--min-pdf-bytes",
        type=int,
        default=2_000,
        help="minimum PDF file size",
    )
    ap.add_argument(
        "--require-png-dpi",
        type=int,
        default=300,
        help="minimum effective DPI for PNGs",
    )
    ap.add_argument(
        "--min-png-width",
        type=int,
        default=1200,
        help="minimum PNG width in pixels",
    )
    ap.add_argument(
        "--min-png-height",
        type=int,
        default=800,
        help="minimum PNG height in pixels",
    )
    ap.add_argument(
        "--require-vector",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="require at least one vector format (pdf/svg) in outputs",
    )
    add_logging_cli_args(ap)
    return ap.parse_args()


def main() -> None:
    args = _parse_args()
    setup_logging_from_args(args)
    analysis_dir = Path(args.analysis_dir)
    report = run_quality_audit(
        analysis_dir,
        figs_dir_name=str(args.figs_dir_name),
        min_png_bytes=int(args.min_png_bytes),
        min_pdf_bytes=int(args.min_pdf_bytes),
        min_png_width=int(args.min_png_width),
        min_png_height=int(args.min_png_height),
        require_png_dpi=int(args.require_png_dpi),
        require_vector=bool(args.require_vector),
    )
    json_path, md_path = write_quality_audit_files(report, analysis_dir=analysis_dir)
    logger.info("wrote %s", json_path)
    logger.info("wrote %s", md_path)


if __name__ == "__main__":
    main()
