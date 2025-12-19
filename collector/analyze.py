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

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import math
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

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
from common.config import load_policy_config_dict
from common.discord_webhook import DiscordWebhookError, send_discord_message
from common.logging_setup import add_logging_cli_args, setup_logging_from_args
from common.metrics import percent_improvement
from common.schema import EventMsg, LinkProfile, PolicyMode

logger = logging.getLogger(__name__)

# ----------------------------- I/O -----------------------------

def _discover_files(inputs: Iterable[str | os.PathLike]) -> list[Path]:
    files: list[Path] = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            # 우선순위: events_*.parquet (rotated) > events.parquet (legacy) > csv > fallback
            cands = list(p.rglob("events_*.parquet"))
            if not cands:
                cands = list(p.rglob("events.parquet"))
            if not cands:
                cands = list(p.rglob("events_*.csv"))
            if not cands:
                cands = list(p.rglob("events.csv"))
            if not cands:
                # 디렉터리에 parquet가 있어도 decisions/markers 같은 비-이벤트 로그는 제외
                skip_prefixes = ("decisions", "markers")
                cands = [f for f in p.rglob("*.parquet") if not f.name.startswith(skip_prefixes)]
            if not cands:
                skip_prefixes = ("decisions", "markers")
                cands = [f for f in p.rglob("*.csv") if not f.name.startswith(skip_prefixes)]
            files.extend(sorted(set(cands)))
        elif p.is_file():
            files.append(p)
    # 중복 제거
    uniq = []
    seen = set()
    for f in files:
        if f.resolve() not in seen:
            uniq.append(f)
            seen.add(f.resolve())
    return uniq


def _discover_named_files(
    inputs: Iterable[str | os.PathLike], *, names: Iterable[str]
) -> list[Path]:
    """
    입력 경로(파일/디렉터리)에서 지정한 파일명들을 재귀적으로 탐색한다.
    - names: 예) ("events_*.parquet", "events.parquet", "events.csv")
    """
    patterns = [str(n) for n in names]
    wanted = set(patterns)
    files: list[Path] = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            for n in wanted:
                files.extend(p.rglob(n))
        elif p.is_file():
            # 파일을 직접 지정한 경우: 본인이 wanted면 포함,
            # 아니면 sibling에 원하는 파일이 있을 수 있으므로 parent를 탐색 루트로 추가.
            if any(fnmatch.fnmatch(p.name, pat) for pat in patterns):
                files.append(p)
            else:
                parent = p.parent
                for n in wanted:
                    files.extend(parent.rglob(n))
    # 중복 제거(해결 경로 기준)
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


def load_events(paths: list[str | os.PathLike]) -> pd.DataFrame:
    """여러 파일에서 Event 레코드를 읽어 단일 DataFrame으로 결합."""
    files = _discover_files(paths)
    if not files:
        raise FileNotFoundError("no input files found (parquet/csv)")

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
        df = _normalize_events_schema(df)
        # 최소 컬럼 확인
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
            # 일부 수집기 버전은 profile/policy를 누락할 수 있으므로 폴더명/manifest 추론 시도
            if {"profile", "policy"} <= missing:
                prof, pol = _infer_profile_policy_from_path(f)
                df["profile"] = prof
                df["policy"] = pol
                missing = required - set(df.columns)
            if missing:
                raise ValueError(f"{f} missing columns: {sorted(missing)}")
        df["__source_file"] = str(f)
        df["run_id"] = _infer_run_id_from_path(f)
        dfs.append(df)

    if not dfs:
        raise RuntimeError("no readable event files")
    out = pd.concat(dfs, ignore_index=True)
    # 타입 캐스팅(안전)
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
        # optional
        "t_recv_ns": "int64",
        "mqtt_bytes": "int64",
    }
    for k, t in cast_cols.items():
        if k in out.columns:
            out[k] = out[k].astype(t)
    out["run_id"] = out.get("run_id", out["__source_file"]).astype("string")
    return out


def load_decisions(paths: list[str | os.PathLike]) -> pd.DataFrame:
    """
    decisions.(parquet|csv) 파일들을 읽어 결합한다.

    필수 컬럼(표준화 후):
    - ts(ns), device_id, state_aoi, state_res, state_res_var, state_loss, state_q_len,
      tau, kbits, reward
    """
    files = _discover_named_files(
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

        df = _normalize_decisions_schema(df)
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
        df["run_id"] = _infer_run_id_from_path(f)
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
        # optional diagnostics
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
    """
    Load Collector meta (logs/collector_meta.json) and expose dedup-related diagnostics.

    Expected keys (collector/collector.py):
    - bytes_total_including_dups
    - dup_bytes_dropped
    - dup_messages_dropped
    """
    files = _discover_named_files(paths, names=("collector_meta.json",))
    if not files:
        return pd.DataFrame()

    rows: list[dict[str, object]] = []
    for f in files:
        try:
            meta = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("failed to read collector meta: %s", f, exc_info=True)
            continue

        run_id = _infer_run_id_from_path(f)
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


def _normalize_decisions_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Collector/버전 차이에 대비한 decisions 스키마 정규화."""
    df = df.copy()
    if "ts" not in df.columns and "ts_ns" in df.columns:
        df["ts"] = df["ts_ns"]
    return df


def enrich_decisions_with_events(decisions: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """
    decisions(PolicyDecision) 로그에 sensor/profile/policy를 보강한다.

    NOTE:
    - PolicyDecisionMsg에는 sensor/profile이 포함되어 있지 않으므로,
      동일 ts/tau/kbits/res의 events 레코드와 매칭하여 추론한다.
    - 매칭 실패 시 sensor/profile/policy는 'unknown'으로 남는다.
    """
    if decisions.empty or events.empty:
        return decisions.copy()

    d = decisions.copy()
    e = events.copy()

    # 키 정규화(부동소수 오차 대비)
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

    # 1차: (run_id, device_id, ts, tau, kbits, res) 완전 매칭
    out = d.merge(
        e_small,
        how="left",
        on=["run_id", "device_id", "ts", "tau_key", "kbits_key", "res_key"],
        suffixes=("", "_ev"),
    )

    # 2차: res까지는 너무 엄격할 수 있으므로, 매칭 실패는 res 제외하고 ts/tau/kbits로 재시도
    miss = out["sensor"].isna()
    if miss.any():
        e_small2 = e_small.drop(columns=["res_key"])
        out2 = d.merge(
            e_small2,
            how="left",
            on=["run_id", "device_id", "ts", "tau_key", "kbits_key"],
            suffixes=("", "_ev"),
        )
        # 기존 out의 매칭 성공분은 유지
        for col in ["sensor", "profile", "policy", "seq", "t_recv_ns_ev"]:
            if col in out2.columns:
                out.loc[miss, col] = out2.loc[miss, col]

    # 기본값
    out["sensor"] = out.get("sensor", "unknown").fillna("unknown").astype("string")
    out["profile"] = out.get("profile", "unknown").fillna("unknown").astype("string")

    # Fallback: if events contain a single sensor/profile per run_id, fill unknown decisions.
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
            out.loc[mask, "profile"] = (
                out.loc[mask, "run_id"].map(profile_map).fillna("unknown")
            )
    # 정책 모드: decision은 adaptive에서만 생성되는 것이 일반적이므로 unknown이면 adaptive로 보정
    out["policy"] = out.get("policy", "adaptive").fillna("adaptive").astype("string")

    # 수신시각: decision 자체 t_recv_ns가 없으면 event의 t_recv_ns로 보강
    if "t_recv_ns" not in out.columns and "t_recv_ns_ev" in out.columns:
        out["t_recv_ns"] = out["t_recv_ns_ev"]
    elif "t_recv_ns" in out.columns and "t_recv_ns_ev" in out.columns:
        out["t_recv_ns"] = out["t_recv_ns"].fillna(out["t_recv_ns_ev"])

    drop_cols = [c for c in ("tau_key", "kbits_key", "res_key", "t_recv_ns_ev") if c in out.columns]
    out.drop(columns=drop_cols, inplace=True)
    return out


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
    """
    LinUCB/파이프라인 진단 지표를 decisions 로그에서 집계한다.

    Returns:
      - diag_by_run: (run_id, profile, policy, sensor) 단위 요약
      - arm_distribution: arm_id 분포(long-form; 논문/보고서용)
      - entropy_windows: 고정 시간창 entropy(long-form)
    """
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

        # --- outbox backlog diagnostics (pending) ---
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

        # --- arm-level diagnostics (arm_id required) ---
        row["linucb_switch_rate"] = float("nan")
        row[entropy_col] = float("nan")
        if "arm_id" in g.columns and g["arm_id"].notna().any():
            ga = g.dropna(subset=["arm_id"]).copy()
            ga["arm_id"] = ga["arm_id"].astype("int64")
            if not ga.empty:
                counts = ga["arm_id"].value_counts().sort_index()
                total = int(counts.sum())
                for arm_id, cnt in counts.items():
                    arm_rows.append(
                        {
                            "run_id": str(run_id),
                            "profile": str(prof),
                            "policy": str(pol),
                            "sensor": str(sensor),
                            "arm_id": int(arm_id),
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

        # --- safe arm intervention diagnostics ---
        row["linucb_safe_forced_rate"] = float("nan")
        if "safe_arm_forced" in g.columns and g["safe_arm_forced"].notna().any():
            s = g["safe_arm_forced"].astype("boolean")
            row["linucb_safe_forced_rate"] = float(s.mean(skipna=True))

        for name, code in [
            ("linucb_forced_reason_none_rate", "NONE"),
            ("linucb_forced_reason_aoi_limit_rate", "AOI_LIMIT"),
            ("linucb_forced_reason_mae_limit_rate", "MAE_LIMIT"),
            ("linucb_forced_reason_both_rate", "BOTH"),
        ]:
            row[name] = float("nan")
        if "forced_reason" in g.columns and g["forced_reason"].notna().any():
            fr = g["forced_reason"].astype("string").fillna("")
            if len(fr) > 0:
                row["linucb_forced_reason_none_rate"] = float((fr == "NONE").mean())
                row["linucb_forced_reason_aoi_limit_rate"] = float((fr == "AOI_LIMIT").mean())
                row["linucb_forced_reason_mae_limit_rate"] = float((fr == "MAE_LIMIT").mean())
                row["linucb_forced_reason_both_rate"] = float((fr == "BOTH").mean())

        # --- UCB decomposition diagnostics ---
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

        # --- reward diagnostics ---
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

        # --- rate-limit skip diagnostics ---
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


def _normalize_events_schema(df: pd.DataFrame) -> pd.DataFrame:
    """
    수집기/버전 차이로 발생하는 컬럼명 차이를 표준 스키마로 맞춘다.
    - ts_ns -> ts
    - mqtt_size_bytes -> mqtt_bytes
    """
    df = df.copy()
    if "ts" not in df.columns and "ts_ns" in df.columns:
        df["ts"] = df["ts_ns"]
    if "mqtt_bytes" not in df.columns and "mqtt_size_bytes" in df.columns:
        df["mqtt_bytes"] = df["mqtt_size_bytes"]
    return df


def _infer_profile_policy_from_path(p: Path) -> tuple[str, str]:
    """
    실험 러너(run_scenarios)가 만든 폴더명 '<profile>__<mode>'에서 추론.
    실패 시 안전값으로 ('unknown','unknown') 반환.
    """
    try:
        # events.* 파일은 보통 ".../<scenario>/logs/events.*" 형태로 저장되므로
        # logs 디렉터리라면 한 단계 위(=scenario dir)에서 이름을 읽는다.
        scenario_dir = p.parent.parent if p.parent.name == "logs" else p.parent
        name = scenario_dir.name
        if "__" in name:
            # "<profile>__<mode>(__repXX...)" 형태까지 허용
            parts = name.split("__")
            prof, mode = parts[0], parts[1]
            # 유효성(열거형)
            prof = LinkProfile(prof).value
            mode = PolicyMode(mode).value
            return prof, mode
    except Exception:
        pass
    return "unknown", "unknown"


def _infer_run_id_from_path(p: Path) -> str:
    """
    분석 입력이 여러 run/시나리오를 섞는 경우를 대비해, 파일 경로에서 run_id를 추론한다.

    우선순위(휴리스틱):
    - .../<scenario>/logs/events*.parquet  → "<run_root>/<scenario>" (run_root가 artifacts가 아니면)
    - artifacts/<run_id>/logs/events*.parquet → "<run_id>"
    - 그 외: "<parent_dir>"

    목적:
    - (device_id, sensor, seq) de-dup이 run 간에 섞여 잘못 제거되는 것을 방지
    - 반복 실험(run replicate) 단위로 지표를 먼저 계산한 뒤 평균/분산을 낼 수 있게 함
    """
    try:
        scenario_dir = p.parent.parent if p.parent.name == "logs" else p.parent
        run_root = scenario_dir.parent
        if run_root.name in {"artifacts", "results", "data", "logs"}:
            return scenario_dir.name
        return f"{run_root.name}/{scenario_dir.name}"
    except Exception:
        return str(p.parent)


# ------------------------- 전처리/유틸 -------------------------

def dedup_and_sort(df: pd.DataFrame) -> pd.DataFrame:
    """
    (run_id, device_id, sensor, seq) 기준 QoS1 중복 제거, ts 오름차순 정렬.
    """
    key = ["run_id", "device_id", "sensor", "seq"]
    if not set(key).issubset(df.columns):
        raise ValueError("dedup requires columns: run_id, device_id, sensor, seq")
    time_col = "t_recv_ns" if "t_recv_ns" in df.columns else "ts"
    # 첫 등장을 유지(또는 ts가 가장 이른 것)
    df = df.sort_values(["run_id", "device_id", "sensor", "seq", time_col], kind="mergesort")
    df = df.drop_duplicates(subset=key, keep="first", ignore_index=True)
    # 전역 시간 정렬(그룹 내 분석 시에는 다시 묶음)
    df = df.sort_values(["run_id", "device_id", "sensor", time_col], kind="mergesort").reset_index(
        drop=True
    )
    return df


def estimate_payload_bytes(df: pd.DataFrame) -> pd.Series:
    """
    EventMsg를 재구성하여 MQTT v3.1.1 PUBLISH 바이트(헤더 포함)를 추정.
    df에 'mqtt_bytes' 또는 'mqtt_size_bytes' 컬럼이 이미 있다면 그대로 사용.
    """
    if "mqtt_bytes" in df.columns:
        s = df["mqtt_bytes"].astype("int64")
        s.name = "mqtt_bytes"
        return s
    if "mqtt_size_bytes" in df.columns:
        s = df["mqtt_size_bytes"].astype("int64")
        s.name = "mqtt_bytes"
        return s

    # 행 단위로 EventMsg 구성 → 크기 계산
    def _calc(row) -> int:
        msg = EventMsg.from_dict({
            "ts": int(row["ts"]),
            "seq": int(row["seq"]),
            "device_id": str(row["device_id"]),
            "sensor": str(row["sensor"]),
            "val": float(row["val"]),
            "pred": float(row["pred"]),
            "res": float(row["res"]),
            "tau": float(row["tau"]),
            "kbits": int(row["kbits"]),
            "profile": str(row["profile"]),
            "policy": str(row["policy"]),
            # aoi_ms는 엣지에서 생략 → 없음
        })
        return int(msg.estimated_mqtt_size(qos=1))

    return df.apply(_calc, axis=1).astype("int64")


# --------------------------- AoI ---------------------------

def _aoi_mean_and_p95_from_segments(
    start_aoi_ms: np.ndarray,
    deltas_ms: np.ndarray,
    *,
    p: float = 0.95,
) -> tuple[float, float]:
    """
    연속시간 AoI 분포(시간 가중)에서 평균과 p-분위수를 계산한다.

    각 구간 i는 길이 Δ_i(ms) 동안 AoI가 start_aoi_ms[i]에서 start_aoi_ms[i]+Δ_i 까지 선형 증가한다.
    시간으로 균일 샘플링한 AoI CDF는 다음을 만족한다:
      Σ clamp(x - start_i, 0, Δ_i) = p · Σ Δ_i
    """
    if start_aoi_ms.size == 0 or deltas_ms.size == 0:
        return float("nan"), float("nan")
    if start_aoi_ms.size != deltas_ms.size:
        raise ValueError("start_aoi_ms and deltas_ms must have same length")

    # 유효 구간만
    mask = np.isfinite(start_aoi_ms) & np.isfinite(deltas_ms) & (deltas_ms > 0)
    a0 = start_aoi_ms[mask].astype("float64")
    d = deltas_ms[mask].astype("float64")
    if d.size == 0:
        return float("nan"), float("nan")

    total = float(np.sum(d))
    if total <= 0:
        return float("nan"), float("nan")

    mean_ms = float((np.sum(a0 * d) + np.sum(d * d) / 2.0) / total)

    if not (0.0 < p < 1.0):
        raise ValueError("p must be in (0,1)")
    target = p * total

    # 이분 탐색: S(x) = Σ clamp(x - a0, 0, d)
    hi = float(np.max(a0 + d))
    lo = 0.0
    if not math.isfinite(hi) or hi <= 0:
        return mean_ms, float("nan")

    for _ in range(60):
        mid = (lo + hi) / 2.0
        s = float(np.sum(np.clip(mid - a0, 0.0, d)))
        if s < target:
            lo = mid
        else:
            hi = mid
    return mean_ms, float(hi)


def aoi_mean_and_p95_from_rx(gen_ns: np.ndarray, recv_ns: np.ndarray) -> tuple[float, float]:
    """
    수신 시각(recv_ns)과 생성 시각(gen_ns)을 이용해 수집기 관점 AoI(ms)의 평균/P95를 계산한다.

    - AoI(t) = t - u(t), u(t)는 '지금까지 수신한 업데이트 중 최신(생성 시각 최대)'의 생성 시각
    - 구간은 recv_i ~ recv_{i+1} 로 정의하며, 구간 시작 AoI는 recv_i - max(gen_0..gen_i).
    """
    if gen_ns.size < 2 or recv_ns.size < 2:
        return float("nan"), float("nan")
    if gen_ns.size != recv_ns.size:
        raise ValueError("gen_ns and recv_ns must have same length")

    gen = gen_ns.astype(np.int64)
    recv = recv_ns.astype(np.int64)
    order = np.argsort(recv, kind="mergesort")
    gen = gen[order]
    recv = recv[order]

    # 최신 업데이트 생성시각(Out-of-order 대비)
    gen_eff = np.maximum.accumulate(gen)
    start_aoi_ms = np.maximum((recv[:-1] - gen_eff[:-1]).astype("float64") / 1e6, 0.0)
    deltas_ms = np.diff(recv.astype(np.int64)).astype("float64") / 1e6
    return _aoi_mean_and_p95_from_segments(start_aoi_ms, deltas_ms, p=0.95)


def aoi_mean_and_p95(ts_ns: np.ndarray) -> tuple[float, float]:
    """
    이벤트 시각(ts_ns, 오름차순)에 대한 평균/95% AoI(ms)를 폐형식으로 계산.
    - 평균:   mean = Σ Δ_i^2 / (2 Σ Δ_i)
    - P95  :  a*   s.t. Σ min(a*, Δ_i) = 0.95 Σ Δ_i   (Δ_i는 ms 단위)
    """
    if ts_ns.size < 2:
        return float("nan"), float("nan")
    # 간격(밀리초)
    deltas_ms = np.diff(ts_ns.astype(np.int64)) / 1e6
    start_aoi_ms = np.zeros_like(deltas_ms, dtype="float64")
    return _aoi_mean_and_p95_from_segments(start_aoi_ms, deltas_ms, p=0.95)


# ------------------------- 집계 로직 -------------------------

def summarize_by_run(df: pd.DataFrame) -> pd.DataFrame:
    """
    run_id × profile × policy × sensor 단위로 Rate/AoI/MAE를 집계.
    - 반복 실험(리플리케이트)이 있는 경우 run 단위 지표를 먼저 만든 뒤,
      그 결과를 평균/분산으로 요약해야 논문/보고서에 적합한 비교가 가능하다.
    """
    need = {"run_id", "ts", "device_id", "sensor", "seq", "profile", "policy", "res", "kbits"}
    if not need.issubset(df.columns):
        missing = sorted(need - set(df.columns))
        raise ValueError(f"missing columns for summarize: {missing}")

    # 중복 제거/정렬
    df = dedup_and_sort(df).copy()
    # MQTT 바이트 추정
    df["mqtt_bytes"] = estimate_payload_bytes(df)

    # 그룹키
    keys = ["run_id", "profile", "policy", "sensor"]

    rows = []
    for (run_id, prof, pol, sensor), g in df.groupby(keys, sort=False):
        # 시간축: 수집기 수신 시각이 있으면 그 기준으로 AoI/Rate를 계산(논문/보고서 관점에 더 적합)
        use_recv = "t_recv_ns" in g.columns and g["t_recv_ns"].notna().any()
        rx_delay_mean_ms = float("nan")
        rx_delay_p50_ms = float("nan")
        rx_delay_p95_ms = float("nan")
        event_reason_threshold_count = float("nan")
        event_reason_heartbeat_count = float("nan")
        event_reason_threshold_frac = float("nan")
        event_reason_heartbeat_frac = float("nan")
        if use_recv:
            g = g.sort_values("t_recv_ns", kind="mergesort")
            recv = g["t_recv_ns"].astype("int64").to_numpy()
            gen = g["ts"].astype("int64").to_numpy()
            if recv.size < 2:
                dur_s = np.nan
                rate = np.nan
                aoi_mean = np.nan
                aoi_p95 = np.nan
            else:
                dur_s = float((recv.max() - recv.min()) / 1e9)
                total_bytes = float(g["mqtt_bytes"].sum())
                rate = (total_bytes / dur_s) if dur_s > 0 else np.nan
                aoi_mean, aoi_p95 = aoi_mean_and_p95_from_rx(gen, recv)
                rx_delay_ms = np.maximum((recv - gen).astype("float64") / 1e6, 0.0)
                if rx_delay_ms.size > 0:
                    rx_delay_mean_ms = float(np.mean(rx_delay_ms))
                    rx_delay_p50_ms = float(np.quantile(rx_delay_ms, 0.50))
                    rx_delay_p95_ms = float(np.quantile(rx_delay_ms, 0.95))
        else:
            ts = np.sort(g["ts"].astype("int64").to_numpy())
            if ts.size < 2:
                dur_s = np.nan
                rate = np.nan
                aoi_mean = np.nan
                aoi_p95 = np.nan
            else:
                dur_s = float((ts.max() - ts.min()) / 1e9)
                total_bytes = float(g["mqtt_bytes"].sum())
                rate = (total_bytes / dur_s) if dur_s > 0 else np.nan
                aoi_mean, aoi_p95 = aoi_mean_and_p95(ts)
        if (not use_recv) and ("t_recv_ns" in g.columns):
            # 수신 시각이 있는데 전부 NaN인 경우 등(타임베이스 명시)
            use_recv = False

        if (not use_recv) and ("t_recv_ns" in g.columns):
            # ts 기반 계산임을 명시하고 싶다면 report에서 안내(여기서는 수치만)
            pass

        if use_recv:
            time_base = "recv"
        else:
            time_base = "ts"

        if "event_reason" in g.columns:
            er = g["event_reason"].astype("string")
            thr_mask = er.isin(["THRESHOLD", "THRESHOLD_OVERRIDE", "SAFETY_AOI"])
            event_reason_threshold_count = float(thr_mask.sum())
            event_reason_heartbeat_count = float((er == "HEARTBEAT").sum())
            if len(g) > 0:
                event_reason_threshold_frac = float(event_reason_threshold_count / len(g))
                event_reason_heartbeat_frac = float(event_reason_heartbeat_count / len(g))

        # 이벤트 기반 MAE 통계(잔차 res)
        mae_mean = float(g["res"].abs().mean())
        mae_p95 = float(g["res"].abs().quantile(0.95)) if len(g) > 0 else np.nan

        # seq gap 기반 샘플 수/전송 비율 추정 (per-sensor seq가 매 샘플 +1 된다는 가정)
        g_seq = g.sort_values("ts", kind="mergesort")
        seq = g_seq["seq"].astype("int64").to_numpy()
        if seq.size == 0:
            n_samples_est = 0
            n_suppressed_est = 0
            send_ratio = float("nan")
        else:
            diffs = np.diff(seq)
            n_suppressed_est = int(np.sum(np.maximum(diffs - 1, 0)))
            n_samples_est = int(seq.size + n_suppressed_est)
            send_ratio = float(seq.size / n_samples_est) if n_samples_est > 0 else float("nan")

        if math.isfinite(dur_s) and dur_s > 0:
            event_rate_hz = float(len(g) / dur_s)
        else:
            event_rate_hz = float("nan")

        rows.append({
            "run_id": str(run_id),
            "profile": str(prof),
            "policy": str(pol),
            "sensor": str(sensor),
            "n_events": int(len(g)),
            "n_samples_est": int(n_samples_est),
            "n_suppressed_est": int(n_suppressed_est),
            "send_ratio": float(send_ratio),
            "duration_s": dur_s,
            "event_rate_hz": float(event_rate_hz),
            "rate_Bps": rate,
            "aoi_mean_ms": aoi_mean,
            "aoi_p95_ms": aoi_p95,
            "mae_event_mean": mae_mean,
            "mae_event_p95": mae_p95,
            "kbits_mean": float(g["kbits"].mean()),
            "time_base": time_base,
            "rx_delay_mean_ms": float(rx_delay_mean_ms),
            "rx_delay_p50_ms": float(rx_delay_p50_ms),
            "rx_delay_p95_ms": float(rx_delay_p95_ms),
            "event_reason_threshold_count": float(event_reason_threshold_count),
            "event_reason_heartbeat_count": float(event_reason_heartbeat_count),
            "event_reason_threshold_frac": float(event_reason_threshold_frac),
            "event_reason_heartbeat_frac": float(event_reason_heartbeat_frac),
        })

    out = pd.DataFrame(rows)
    # 정렬: profile, policy(고정 순서), sensor
    pol_order = ["periodic", "fixed_tau", "adaptive"]
    out["policy"] = pd.Categorical(out["policy"], categories=pol_order, ordered=True)
    out = out.sort_values(["run_id", "profile", "policy", "sensor"]).reset_index(drop=True)
    return out


def summarize(summary_by_run: pd.DataFrame) -> pd.DataFrame:
    """
    profile × policy × sensor 단위로 (run 단위 지표의) 평균/표준편차를 요약.

    반환 컬럼:
    - rate_Bps, aoi_mean_ms 등: run 평균(mean)
    - *_std: run 표준편차(std; n_runs<2이면 NaN)
    - n_events: 전체 이벤트 수 합(sum)  (참고용)
    - n_runs: 리플리케이트 수(count)
    """
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
    pol_order = ["periodic", "fixed_tau", "adaptive"]
    out["policy"] = pd.Categorical(out["policy"], categories=pol_order, ordered=True)
    out = out.sort_values(["profile", "policy", "sensor"]).reset_index(drop=True)
    return out


def compare_policies(
    summary: pd.DataFrame,
    *,
    baseline_policy: str = "periodic",
) -> pd.DataFrame:
    """
    baseline_policy(기본: periodic) 대비 개선/변화량을 계산해 비교 테이블을 만든다.

    - improvement(%)는 "낮을수록 좋은 지표" 기준으로 계산:
      improvement = (baseline - candidate) / baseline * 100
      → Rate/AoI/MAE 모두 '작을수록 좋다' 가정이며, MAE는 보통 음수(=악화)가 발생할 수 있다.
    """
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

        for col, unit in [
            ("rate_Bps", "Bps"),
            ("aoi_mean_ms", "ms"),
            ("aoi_p95_ms", "ms"),
            ("mae_event_mean", "mae"),
            ("mae_event_p95", "mae"),
        ]:
            cand = float(r[col])
            basev = float(row[f"baseline_{col}"])
            row[f"{col}_delta_{unit}"] = cand - basev
            row[f"{col}_improvement_pct"] = percent_improvement(basev, cand)

        rows.append(row)

    out = pd.DataFrame(rows)
    pol_order = ["periodic", "fixed_tau", "adaptive"]
    out["policy"] = pd.Categorical(out["policy"], categories=pol_order, ordered=True)
    out = out.sort_values(["profile", "sensor", "policy"]).reset_index(drop=True)
    return out


# --------------------------- 리포트 ---------------------------

def _write_report_md(
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

    # --- LinUCB diagnostics (optional) ---
    diag_cols = [
        "linucb_n_decisions",
        "linucb_action_entropy_mean_60s",
        "linucb_switch_rate",
        "linucb_safe_forced_rate",
        "linucb_forced_reason_aoi_limit_rate",
        "linucb_forced_reason_mae_limit_rate",
        "linucb_forced_reason_both_rate",
        "linucb_ucb_uncertainty_mean",
        "linucb_rate_limit_skips_per_decision",
        "outbox_pending_max",
        "outbox_pending_auc_s",
        "outbox_pending_recovery_s",
        "dup_bytes_ratio",
        "rx_delay_p50_ms",
        "rx_delay_p95_ms",
    ]
    if any(c in tbl.columns for c in diag_cols):
        diag = tbl[tbl["policy"].astype("string") == "adaptive"].copy()
        if not diag.empty:
            lines.append("")
            lines.append("## LinUCB/파이프라인 진단 (adaptive)")
            lines.append("")
            lines.append(
                "| profile | sensor | n_dec | H(60s) | switch | safe_forced | "
                "AOI_limit | MAE_limit | BOTH | UCB_u_mean | skip/dec | "
                "q_max | q_auc[count·s] | q_recover[s] | dup_bytes_ratio[%] | "
                "rx_p50[ms] | rx_p95[ms] |"
            )
            lines.append(
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"
            )
            for _, r in diag.iterrows():
                n_dec = (
                    _fmt_mean_std("linucb_n_decisions", ".0f")
                    if "linucb_n_decisions" in r
                    else "NaN"
                )
                h_60s = (
                    _fmt_mean_std("linucb_action_entropy_mean_60s", ".3f")
                    if "linucb_action_entropy_mean_60s" in r
                    else "NaN"
                )
                switch = (
                    _fmt_mean_std("linucb_switch_rate", ".3f")
                    if "linucb_switch_rate" in r
                    else "NaN"
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
                q_max = (
                    _fmt_mean_std("outbox_pending_max", ".1f")
                    if "outbox_pending_max" in r
                    else "NaN"
                )
                q_auc = (
                    _fmt_mean_std("outbox_pending_auc_s", ".1f")
                    if "outbox_pending_auc_s" in r
                    else "NaN"
                )
                q_rec = (
                    _fmt_mean_std("outbox_pending_recovery_s", ".1f")
                    if "outbox_pending_recovery_s" in r
                    else "NaN"
                )
                dup = (
                    _fmt_pct_mean_std("dup_bytes_ratio", ".1f") if "dup_bytes_ratio" in r else "NaN"
                )
                rx_p50 = (
                    _fmt_mean_std("rx_delay_p50_ms", ".1f") if "rx_delay_p50_ms" in r else "NaN"
                )
                rx_p95 = (
                    _fmt_mean_std("rx_delay_p95_ms", ".1f") if "rx_delay_p95_ms" in r else "NaN"
                )
                cells = [
                    str(r["profile"]),
                    str(r["sensor"]),
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
    lines.append("### Adaptive vs periodic interpretation")
    lines.append("")
    lines.append("Lower is better for Rate/AoI/MAE; positive improvement means better.")
    comp_adapt = comparisons[comparisons["policy"].astype("string") == "adaptive"]
    if comp_adapt.empty:
        lines.append("- adaptive: no comparison rows available (baseline missing).")
    else:
        for _, r in comp_adapt.iterrows():
            prof = str(r.get("profile", ""))
            sensor = str(r.get("sensor", ""))
            rate_imp = float(r.get("rate_Bps_improvement_pct", float("nan")))
            aoi_imp = float(r.get("aoi_mean_ms_improvement_pct", float("nan")))
            mae_imp = float(r.get("mae_event_mean_improvement_pct", float("nan")))
            if not (math.isfinite(rate_imp) and math.isfinite(aoi_imp) and math.isfinite(mae_imp)):
                lines.append(
                    f"- {prof}/{sensor}: insufficient baseline or non-finite metrics; "
                    "cannot conclude improvement."
                )
                continue
            rate_txt = f"{rate_imp:+.1f}%"
            aoi_txt = f"{aoi_imp:+.1f}%"
            mae_txt = f"{mae_imp:+.1f}%"
            tradeoffs = []
            if rate_imp < 0:
                tradeoffs.append("rate")
            if aoi_imp < 0:
                tradeoffs.append("AoI")
            if mae_imp < 0:
                tradeoffs.append("MAE")
            if tradeoffs:
                tradeoff_txt = ", ".join(tradeoffs)
                lines.append(
                    f"- {prof}/{sensor}: rate {rate_txt}, AoI mean {aoi_txt}, "
                    f"MAE mean {mae_txt} (tradeoff in: {tradeoff_txt})."
                )
            else:
                lines.append(
                    f"- {prof}/{sensor}: rate {rate_txt}, AoI mean {aoi_txt}, "
                    f"MAE mean {mae_txt} (all improved)."
                )

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


# --------------------------- Plotting ---------------------------

@dataclass(frozen=True)
class PlotConfig:
    """Plot output configuration (paper-ready)."""

    dir_name: str = "figs"
    formats: tuple[str, ...] = ("png", "pdf")
    dpi: int = 300


def _parse_plot_formats(raw: str) -> tuple[str, ...]:
    items = [x.strip().lower() for x in str(raw).split(",") if x.strip()]
    if not items:
        return ("png",)
    allowed = {"png", "pdf", "svg"}
    bad = [x for x in items if x not in allowed]
    if bad:
        raise ValueError(f"invalid --plot-formats: {bad} (allowed: {sorted(allowed)})")
    # de-dup while preserving order
    out: list[str] = []
    for x in items:
        if x not in out:
            out.append(x)
    return tuple(out)


def _slug(x: str) -> str:
    return (
        str(x)
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
        .replace(":", "_")
        .replace("|", "_")
    )


def _fig_basename(
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


def _maybe_import_matplotlib():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore

        return matplotlib, plt
    except Exception:
        return None, None


def _apply_plot_style(matplotlib) -> None:
    # Keep it deterministic and paper-friendly without seaborn.
    matplotlib.rcParams.update(
        {
            "figure.dpi": 120,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.02,
            "axes.titlesize": 12,
            "axes.labelsize": 11,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 10,
            "lines.linewidth": 1.8,
            "axes.grid": False,
            "grid.alpha": 0.25,
        }
    )


# plot manifest (for audits; populated by _save_figure_multi)
_PLOT_MANIFEST: list[dict[str, object]] = []


def _save_figure_multi(fig, out_dir: Path, *, base_name: str, cfg: PlotConfig) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        fig.tight_layout()
    except Exception:
        pass
    created: list[Path] = []
    for fmt in cfg.formats:
        out_path = out_dir / f"{base_name}.{fmt}"
        save_kwargs = {
            "bbox_inches": "tight",
            "pad_inches": 0.02,
            "facecolor": "white",
        }
        if fmt == "png":
            fig.savefig(out_path, dpi=int(cfg.dpi), **save_kwargs)
        else:
            fig.savefig(out_path, **save_kwargs)
        created.append(out_path)

    try:
        size_in = fig.get_size_inches()
        axes = []
        for ax in fig.get_axes():
            axes.append(
                {
                    "title": str(ax.get_title() or ""),
                    "xlabel": str(ax.get_xlabel() or ""),
                    "ylabel": str(ax.get_ylabel() or ""),
                    "ax_label": str(ax.get_label() or ""),
                }
            )
        _PLOT_MANIFEST.append(
            {
                "base_name": base_name,
                "formats": list(cfg.formats),
                "dpi": int(cfg.dpi),
                "size_inches": [float(size_in[0]), float(size_in[1])],
                "files": [p.name for p in created],
                "axes": axes,
            }
        )
    except Exception:
        pass
    return created


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
                bp = ax.boxplot(
                    data,
                    labels=labels,
                    showfliers=False,
                    patch_artist=True,
                    medianprops={"color": "#111827", "linewidth": 1.8},
                    boxprops={"edgecolor": "#374151"},
                    whiskerprops={"color": "#374151"},
                    capprops={"color": "#374151"},
                )
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
                bp = ax.boxplot(
                    data,
                    labels=labels,
                    showfliers=False,
                    patch_artist=True,
                    medianprops={"color": "#111827", "linewidth": 1.8},
                    boxprops={"edgecolor": "#374151"},
                    whiskerprops={"color": "#374151"},
                    capprops={"color": "#374151"},
                )
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


def _fmt_num(value, fmt: str) -> str:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "NaN"
    if not math.isfinite(num):
        return "NaN"
    return format(num, fmt)


def format_summary_for_discord(summary: pd.DataFrame, *, limit: int = 10) -> str:
    """Create a compact Discord-friendly text report from the summary table."""

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
    args = parse_args()
    setup_logging_from_args(args)
    _PLOT_MANIFEST.clear()
    out_dir = Path(args.out)
    plot_cfg = PlotConfig(
        dir_name=str(args.plot_dir),
        formats=_parse_plot_formats(str(args.plot_formats)),
        dpi=int(args.plot_dpi),
    )

    df = load_events(args.input)
    df = dedup_and_sort(df)
    try:
        decisions = load_decisions(args.input)
    except Exception:
        logger.exception("failed to load decisions logs")
        decisions = pd.DataFrame()

    meta = load_collector_meta(args.input)
    by_run = summarize_by_run(df)
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
    baseline_policy = str(args.baseline_policy)
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
    if args.save_parquet:
        try:
            pq_path = out_dir / "metrics_summary.parquet"
            summary.to_parquet(pq_path, index=False)
        except Exception:
            pass

    figures: list[Path] = []
    paper_figures: list[Path] = []
    diag_figures: list[Path] = []
    if bool(args.plots):
        figures = _try_make_plots(
            out_dir,
            summary,
            plot_cfg=plot_cfg,
            pareto_p95=bool(args.pareto_p95),
        )
        figures.extend(
            _try_make_pipeline_plots(
                out_dir,
                events=df,
                decisions_enriched=decisions_enriched,
                by_run=by_run,
                plot_cfg=plot_cfg,
            )
        )
        if bool(args.paper_plots):
            try:
                paper_figures = _try_make_paper_plots(
                    out_dir,
                    events=df,
                    decisions=decisions,
                    summary=summary,
                    plot_cfg=plot_cfg,
                    policy_config_path=str(args.policy_config),
                    reward_window=int(args.reward_window),
                    action_bins=int(args.action_bins),
                    top_actions=int(args.top_actions),
                    cellular_var_period_s=int(args.cellular_var_period_s),
                )
            except Exception:
                logger.exception("failed to generate paper plots")
                paper_figures = []
        if bool(args.diagnostic_plots):
            try:
                diag_figures = _try_make_diagnostic_plots(
                    out_dir,
                    events=df,
                    decisions_enriched=decisions_enriched,
                    by_run=by_run,
                    summary=summary,
                    arm_distribution=arm_dist,
                    entropy_windows=entropy_win,
                    plot_cfg=plot_cfg,
                    arm_top_n=int(args.arm_top_n),
                    entropy_smooth_window=int(args.entropy_smooth_window),
                    ucb_timeseries=bool(args.ucb_timeseries),
                )
            except Exception:
                logger.exception("failed to generate diagnostic plots")
                diag_figures = []

    _write_report_md(
        out_dir,
        summary,
        comparisons=comparisons,
        baseline_policy=baseline_policy,
        figures_dir=str(plot_cfg.dir_name),
    )

    if _PLOT_MANIFEST:
        try:
            manifest_path = out_dir / "plot_manifest.json"
            manifest = {
                "plot_cfg": {
                    "dir_name": str(plot_cfg.dir_name),
                    "formats": list(plot_cfg.formats),
                    "dpi": int(plot_cfg.dpi),
                },
                "figures": _PLOT_MANIFEST,
            }
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
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
