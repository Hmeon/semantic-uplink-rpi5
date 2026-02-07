"""Input discovery/loading and schema-normalization helpers for analyzer."""

from __future__ import annotations

import fnmatch
import json
import logging
import math
import os
from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from common.schema import LinkProfile, PolicyMode

logger = logging.getLogger(__name__)


def infer_run_dir_from_file(p: Path) -> Path:
    """Return the run directory that contains `logs/` for a given log file path."""
    return p.parent.parent if p.parent.name == "logs" else p.parent


def read_json_best_effort(p: Path) -> dict | None:
    """Best-effort JSON reader that returns None on any parse/read error."""
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def extract_run_meta(run_dir: Path) -> dict[str, object]:
    """Extract best-effort run metadata (seed/scenario) for alignment diagnostics."""
    meta: dict[str, object] = {}

    run_meta = read_json_best_effort(run_dir / "run_meta.json")
    if isinstance(run_meta, dict):
        seed = run_meta.get("seed")
        if isinstance(seed, (int, float)) and math.isfinite(float(seed)):
            meta["meta_seed"] = int(seed)
        scenario = run_meta.get("scenario")
        if isinstance(scenario, dict):
            sid = scenario.get("id")
            if isinstance(sid, str) and sid.strip():
                meta["meta_scenario"] = sid.strip()
        elif isinstance(scenario, str) and scenario.strip():
            meta["meta_scenario"] = scenario.strip()

    if "meta_seed" not in meta or "meta_scenario" not in meta:
        col_meta = read_json_best_effort(run_dir / "logs" / "collector_meta.json")
        if isinstance(col_meta, dict):
            synth = col_meta.get("synthetic")
            if isinstance(synth, dict):
                if "meta_seed" not in meta:
                    seed = synth.get("seed")
                    if isinstance(seed, (int, float)) and math.isfinite(float(seed)):
                        meta["meta_seed"] = int(seed)
                if "meta_scenario" not in meta:
                    sid = synth.get("scenario") or synth.get("scenario_id")
                    if isinstance(sid, str) and sid.strip():
                        meta["meta_scenario"] = sid.strip()

    return meta


def discover_files(inputs: Iterable[str | os.PathLike]) -> list[Path]:
    """Discover event files from file/directory inputs with priority order."""
    files: list[Path] = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            cands = list(p.rglob("events_*.parquet"))
            if not cands:
                cands = list(p.rglob("events.parquet"))
            if not cands:
                cands = list(p.rglob("events_*.csv"))
            if not cands:
                cands = list(p.rglob("events.csv"))
            if not cands:
                skip_prefixes = ("decisions", "markers")
                cands = [f for f in p.rglob("*.parquet") if not f.name.startswith(skip_prefixes)]
            if not cands:
                skip_prefixes = ("decisions", "markers")
                cands = [f for f in p.rglob("*.csv") if not f.name.startswith(skip_prefixes)]
            files.extend(sorted(set(cands)))
        elif p.is_file():
            files.append(p)
    uniq: list[Path] = []
    seen: set[Path] = set()
    for f in files:
        key = f.resolve()
        if key not in seen:
            uniq.append(f)
            seen.add(key)
    return uniq


def discover_named_files(
    inputs: Iterable[str | os.PathLike], *, names: Iterable[str]
) -> list[Path]:
    """Discover files matching explicit names/patterns from file/directory inputs."""
    patterns = [str(n) for n in names]
    wanted = set(patterns)
    files: list[Path] = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            for n in wanted:
                files.extend(p.rglob(n))
        elif p.is_file():
            if any(fnmatch.fnmatch(p.name, pat) for pat in patterns):
                files.append(p)
            else:
                parent = p.parent
                for n in wanted:
                    files.extend(parent.rglob(n))
    uniq: list[Path] = []
    seen: set[Path] = set()
    for f in files:
        try:
            key = f.resolve()
        except Exception:
            key = f
        if key not in seen:
            uniq.append(f)
            seen.add(key)
    return sorted(uniq)


def normalize_events_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize event schema aliases (ts_ns->ts, mqtt_size_bytes->mqtt_bytes)."""
    out = df.copy()
    if "ts" not in out.columns and "ts_ns" in out.columns:
        out["ts"] = out["ts_ns"]
    if "mqtt_bytes" not in out.columns and "mqtt_size_bytes" in out.columns:
        out["mqtt_bytes"] = out["mqtt_size_bytes"]
    return out


def infer_profile_policy_from_path(p: Path) -> tuple[str, str]:
    """Infer (profile, policy) from '<profile>__<mode>' scenario directory names."""
    try:
        scenario_dir = p.parent.parent if p.parent.name == "logs" else p.parent
        name = scenario_dir.name
        if "__" in name:
            parts = name.split("__")
            prof, mode = parts[0], parts[1]
            prof = LinkProfile(prof).value
            mode = PolicyMode(mode).value
            return prof, mode
    except Exception:
        pass
    return "unknown", "unknown"


def infer_run_id_from_path(p: Path) -> str:
    """Infer run id from run/scenario path layout to avoid cross-run dedup bleed."""
    try:
        scenario_dir = p.parent.parent if p.parent.name == "logs" else p.parent
        run_root = scenario_dir.parent
        if run_root.name in {"artifacts", "results", "data", "logs"}:
            return scenario_dir.name
        return f"{run_root.name}/{scenario_dir.name}"
    except Exception:
        return str(p.parent)


def normalize_decisions_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize decision schema aliases (ts_ns->ts)."""
    out = df.copy()
    if "ts" not in out.columns and "ts_ns" in out.columns:
        out["ts"] = out["ts_ns"]
    return out


def load_events(paths: list[str | os.PathLike]) -> pd.DataFrame:
    """Load event records from parquet/CSV inputs into a normalized DataFrame."""
    files = discover_files(paths)
    if not files:
        raise FileNotFoundError("no input files found (parquet/csv)")

    dfs = []
    meta_cache: dict[Path, dict[str, object]] = {}
    for f in files:
        if f.suffix.lower() == ".parquet":
            try:
                df = pd.read_parquet(f)
            except Exception as e:
                raise RuntimeError(
                    f"failed to read parquet: {f} (install pyarrow). root_error={e}"
                ) from e
        elif f.suffix.lower() == ".csv":
            df = pd.read_csv(f)
        else:
            continue

        df = normalize_events_schema(df)
        required = {
            "ts",
            "seq",
            "device_id",
            "sensor",
            "val",
            "pred",
            "res",
            "tau",
            "kbits",
            "profile",
            "policy",
        }
        missing = required - set(df.columns)
        if missing:
            if {"profile", "policy"} <= missing:
                prof, pol = infer_profile_policy_from_path(f)
                df["profile"] = prof
                df["policy"] = pol
                missing = required - set(df.columns)
            if missing:
                raise ValueError(f"{f} missing columns: {sorted(missing)}")

        df["__source_file"] = str(f)
        df["run_id"] = infer_run_id_from_path(f)

        run_dir = infer_run_dir_from_file(f)
        meta = meta_cache.get(run_dir)
        if meta is None:
            meta = extract_run_meta(run_dir)
            meta_cache[run_dir] = meta
        if "meta_seed" in meta:
            df["meta_seed"] = int(meta["meta_seed"])
        if "meta_scenario" in meta:
            df["meta_scenario"] = str(meta["meta_scenario"])
        dfs.append(df)

    if not dfs:
        raise RuntimeError("no readable event files")
    out = pd.concat(dfs, ignore_index=True)
    cast_cols = {
        "ts": "int64",
        "seq": "uint64",
        "device_id": "string",
        "sensor": "string",
        "val": "float64",
        "pred": "float64",
        "res": "float64",
        "tau": "float64",
        "kbits": "int64",
        "profile": "string",
        "policy": "string",
        "t_recv_ns": "Int64",
        "mqtt_bytes": "Int64",
        "meta_seed": "Int64",
        "meta_scenario": "string",
    }
    for k, t in cast_cols.items():
        if k in out.columns:
            out[k] = out[k].astype(t)
    out["run_id"] = out.get("run_id", out["__source_file"]).astype("string")
    return out


def load_decisions(paths: list[str | os.PathLike]) -> pd.DataFrame:
    """Load LinUCB decision logs into a normalized DataFrame."""
    files = discover_named_files(
        paths,
        names=(
            "decisions_*.parquet",
            "decisions.parquet",
            "decisions_*.csv",
            "decisions.csv",
        ),
    )
    if not files:
        return pd.DataFrame()

    dfs = []
    for f in files:
        if f.suffix.lower() == ".parquet":
            try:
                df = pd.read_parquet(f)
            except Exception as e:
                raise RuntimeError(
                    f"failed to read parquet: {f} (install pyarrow). root_error={e}"
                ) from e
        elif f.suffix.lower() == ".csv":
            df = pd.read_csv(f)
        else:
            continue

        df = normalize_decisions_schema(df)
        required = {
            "ts",
            "device_id",
            "state_aoi",
            "state_res",
            "state_res_var",
            "state_loss",
            "state_q_len",
            "tau",
            "kbits",
            "reward",
        }
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"{f} missing columns: {sorted(missing)}")

        df["__source_file"] = str(f)
        df["run_id"] = infer_run_id_from_path(f)
        dfs.append(df)

    if not dfs:
        return pd.DataFrame()
    out = pd.concat(dfs, ignore_index=True)
    cast_cols = {
        "ts": "int64",
        "t_recv_ns": "int64",
        "device_id": "string",
        "state_aoi": "float64",
        "state_res": "float64",
        "state_res_var": "float64",
        "state_loss": "float64",
        "state_q_len": "int64",
        "tau": "float64",
        "kbits": "int64",
        "reward": "float64",
        "topic": "string",
        "arm_id": "Int64",
        "safe_arm_forced": "boolean",
        "forced_reason": "string",
        "ucb_exploitation": "float64",
        "ucb_exploration": "float64",
        "ucb_score": "float64",
        "ucb_alpha": "float64",
        "reward_aoi": "float64",
        "reward_mae": "float64",
        "reward_rate": "float64",
        "rate_limit_skips": "Int64",
        "t_predict_ms": "float64",
        "t_decide_ms": "float64",
        "t_observe_ms": "float64",
        "t_step_ms": "float64",
        "cpu_step_ms": "float64",
        "maxrss_kb": "float64",
    }
    for k, t in cast_cols.items():
        if k in out.columns:
            out[k] = out[k].astype(t)
    out["run_id"] = out.get("run_id", out["__source_file"]).astype("string")
    return out


def load_collector_meta(paths: list[str | os.PathLike]) -> pd.DataFrame:
    """Load collector metadata logs into per-run dedup/bytes DataFrame."""
    files = discover_named_files(paths, names=("collector_meta.json",))
    if not files:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for f in files:
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("failed to read collector meta: %s", f, exc_info=True)
            continue

        run_id = infer_run_id_from_path(f)
        bytes_total = meta.get("bytes_total_including_dups", None)
        dup_bytes = meta.get("dup_bytes_dropped", None)
        dup_msgs = meta.get("dup_messages_dropped", None)

        bytes_total_f = float(bytes_total) if bytes_total is not None else float("nan")
        dup_bytes_f = float(dup_bytes) if dup_bytes is not None else float("nan")
        dup_bytes_ratio = float("nan")
        if (
            math.isfinite(bytes_total_f)
            and bytes_total_f > 0
            and math.isfinite(dup_bytes_f)
            and dup_bytes_f >= 0
        ):
            dup_bytes_ratio = float(dup_bytes_f / bytes_total_f)

        rows.append(
            {
                "run_id": str(run_id),
                "bytes_total_including_dups": bytes_total_f,
                "dup_bytes_dropped": dup_bytes_f,
                "dup_bytes_ratio": float(dup_bytes_ratio),
                "dup_messages_dropped": float(dup_msgs) if dup_msgs is not None else float("nan"),
            }
        )

    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    for c, t in {
        "run_id": "string",
        "bytes_total_including_dups": "float64",
        "dup_bytes_dropped": "float64",
        "dup_bytes_ratio": "float64",
        "dup_messages_dropped": "float64",
    }.items():
        if c in out.columns:
            out[c] = out[c].astype(t)
    out = out.drop_duplicates(subset=["run_id"], keep="last", ignore_index=True)
    return out


def enrich_decisions_with_events(decisions: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Backfill sensor/profile/policy fields in decisions using event logs."""
    if decisions.empty or events.empty:
        return decisions.copy()

    d = decisions.copy()
    e = events.copy()

    def _tau_key(x: pd.Series) -> pd.Series:
        return x.astype("float64").round(6)

    def _res_key(x: pd.Series) -> pd.Series:
        return x.astype("float64").round(6)

    d["tau_key"] = _tau_key(d["tau"])
    d["kbits_key"] = d["kbits"].astype("int64")
    d["res_key"] = _res_key(d["state_res"])

    e["tau_key"] = _tau_key(e["tau"])
    e["kbits_key"] = e["kbits"].astype("int64")
    e["res_key"] = _res_key(e["res"])

    e_small = e[
        [
            "run_id",
            "device_id",
            "ts",
            "tau_key",
            "kbits_key",
            "res_key",
            "sensor",
            "profile",
            "policy",
            "seq",
            "t_recv_ns",
        ]
    ].copy()

    out = d.merge(
        e_small,
        how="left",
        on=["run_id", "device_id", "ts", "tau_key", "kbits_key", "res_key"],
        suffixes=("", "_ev"),
    )

    miss = out["sensor"].isna()
    if miss.any():
        e_small2 = e_small.drop(columns=["res_key"])
        out2 = d.merge(
            e_small2,
            how="left",
            on=["run_id", "device_id", "ts", "tau_key", "kbits_key"],
            suffixes=("", "_ev"),
        )
        for col in ["sensor", "profile", "policy", "seq", "t_recv_ns_ev", "t_recv_ns"]:
            if col in out2.columns:
                out.loc[miss, col] = out2.loc[miss, col]

    out["sensor"] = out.get("sensor", "unknown").fillna("unknown").astype("string")
    out["profile"] = out.get("profile", "unknown").fillna("unknown").astype("string")

    run_sensor = (
        e.groupby("run_id")["sensor"].agg(lambda s: s.dropna().unique())
        if "run_id" in e.columns and "sensor" in e.columns
        else None
    )
    if run_sensor is not None:
        sensor_map = {rid: vals[0] for rid, vals in run_sensor.items() if len(vals) == 1}
        if sensor_map:
            mask = out["sensor"] == "unknown"
            out.loc[mask, "sensor"] = out.loc[mask, "run_id"].map(sensor_map).fillna("unknown")

    run_profile = (
        e.groupby("run_id")["profile"].agg(lambda s: s.dropna().unique())
        if "run_id" in e.columns and "profile" in e.columns
        else None
    )
    if run_profile is not None:
        profile_map = {rid: vals[0] for rid, vals in run_profile.items() if len(vals) == 1}
        if profile_map:
            mask = out["profile"] == "unknown"
            out.loc[mask, "profile"] = out.loc[mask, "run_id"].map(profile_map).fillna("unknown")

    out["policy"] = out.get("policy", "adaptive").fillna("adaptive").astype("string")

    if "t_recv_ns" not in out.columns and "t_recv_ns_ev" in out.columns:
        out["t_recv_ns"] = out["t_recv_ns_ev"]
    elif "t_recv_ns" in out.columns and "t_recv_ns_ev" in out.columns:
        out["t_recv_ns"] = out["t_recv_ns"].fillna(out["t_recv_ns_ev"])

    drop_cols = [c for c in ("tau_key", "kbits_key", "res_key", "t_recv_ns_ev") if c in out.columns]
    out.drop(columns=drop_cols, inplace=True)
    return out


__all__ = [
    "discover_files",
    "discover_named_files",
    "enrich_decisions_with_events",
    "extract_run_meta",
    "infer_profile_policy_from_path",
    "infer_run_dir_from_file",
    "infer_run_id_from_path",
    "load_collector_meta",
    "load_decisions",
    "load_events",
    "normalize_decisions_schema",
    "normalize_events_schema",
    "read_json_best_effort",
]
