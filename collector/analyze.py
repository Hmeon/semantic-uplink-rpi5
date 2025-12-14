# collector/analyze.py
# Python 3.10+
# 목적: 수집된 Event 로그를 읽어 Rate/AoI/MAE를 시나리오별로 집계하고,
#       표/파레토 분석에 필요한 요약 CSV/Parquet/Markdown을 생성한다.
#
# 입력 기대:
#  - Parquet: *.parquet (권장, 빠름)
#  - CSV    : *.csv (utf-8, 헤더 포함)
#  - 파일/디렉터리 모두 허용. 디렉터리는 재귀 검색하며 events.(parquet|csv) 우선.
#  - 필수 컬럼(스키마 정합, 표준화 후):
#      ts(ns), seq, device_id, sensor, val, pred, res, tau, kbits, profile, policy
#    * Collector가 저장한 events.parquet는 ts 대신 ts_ns를 쓰는 경우가 있어 자동 변환한다.
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
import json
import math
import os
from pathlib import Path
from typing import Iterable, List

import numpy as np
import pandas as pd

from common.discord_webhook import DiscordWebhookError, send_discord_message
from common.schema import EventMsg, SensorType, LinkProfile, PolicyMode
from common.metrics import percent_improvement


# ----------------------------- I/O -----------------------------

def _discover_files(inputs: Iterable[str | os.PathLike]) -> List[Path]:
    files: List[Path] = []
    for inp in inputs:
        p = Path(inp)
        if p.is_dir():
            # 우선순위: events.parquet > *.parquet > *.csv
            cands = list(p.rglob("events.parquet"))
            if not cands:
                cands = list(p.rglob("*.parquet"))
            if not cands:
                cands = list(p.rglob("*.csv"))
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


def load_events(paths: List[str | os.PathLike]) -> pd.DataFrame:
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
    - .../<scenario>/logs/events.parquet  → "<run_root>/<scenario>" (run_root가 artifacts가 아니면)
    - artifacts/<run_id>/logs/events.parquet → "<run_id>"
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

        # 이벤트 기반 MAE 통계(잔차 res)
        mae_mean = float(g["res"].abs().mean())
        mae_p95 = float(g["res"].abs().quantile(0.95)) if len(g) > 0 else np.nan

        rows.append({
            "run_id": str(run_id),
            "profile": str(prof),
            "policy": str(pol),
            "sensor": str(sensor),
            "n_events": int(len(g)),
            "duration_s": dur_s,
            "rate_Bps": rate,
            "aoi_mean_ms": aoi_mean,
            "aoi_p95_ms": aoi_p95,
            "mae_event_mean": mae_mean,
            "mae_event_p95": mae_p95,
            "kbits_mean": float(g["kbits"].mean()),
            "time_base": time_base,
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

    rows = []
    for (prof, pol, sensor), g in summary_by_run.groupby(["profile", "policy", "sensor"], sort=False):
        n_runs = int(g["run_id"].nunique())
        row = {
            "profile": str(prof),
            "policy": str(pol),
            "sensor": str(sensor),
            "n_runs": n_runs,
            "n_events": int(g["n_events"].sum()),
        }
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

    required = {"profile", "policy", "sensor", "rate_Bps", "aoi_mean_ms", "aoi_p95_ms", "mae_event_mean", "mae_event_p95"}
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
    figures_dir: str = "figures",
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "report.md"
    lines = []
    lines.append("# 실험 요약 리포트")
    lines.append("")
    lines.append("본 리포트는 **이벤트 기반 MAE(res)**를 사용합니다. 전체 시계열 MAE가 필요하면 원시 스트림 또는 복원 파이프가 추가로 필요합니다.")
    lines.append("")
    lines.append("- AoI/Rate는 기본적으로 **수집기 수신 시각(`t_recv_ns`)** 기반으로 계산합니다(없으면 `ts` 기반).")
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
    # n_runs가 있으면 함께 표시
    has_runs = "n_runs" in tbl.columns
    if has_runs:
        lines.append("| profile | policy | sensor | n_runs | n_events | dur[s] | rate[B/s] | AoI_mean[ms] | AoI_p95[ms] | MAE_event_mean | MAE_event_p95 | k̄ |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    else:
        lines.append("| profile | policy | sensor | n_events | dur[s] | rate[B/s] | AoI_mean[ms] | AoI_p95[ms] | MAE_event_mean | MAE_event_p95 | k̄ |")
        lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in tbl.iterrows():
        if has_runs:
            lines.append("| {profile} | {policy} | {sensor} | {n_runs} | {n_events} | {duration_s} | {rate_Bps} | {aoi_mean_ms} | {aoi_p95_ms} | {mae_event_mean} | {mae_event_p95} | {kbits_mean} |".format(
                profile=r["profile"], policy=r["policy"], sensor=r["sensor"],
                n_runs=int(r.get("n_runs", 0)),
                n_events=int(r["n_events"]),
                duration_s=_fmt_mean_std("duration_s", ".1f"),
                rate_Bps=_fmt_mean_std("rate_Bps", ".1f"),
                aoi_mean_ms=_fmt_mean_std("aoi_mean_ms", ".1f"),
                aoi_p95_ms=_fmt_mean_std("aoi_p95_ms", ".1f"),
                mae_event_mean=_fmt_mean_std("mae_event_mean", ".3f"),
                mae_event_p95=_fmt_mean_std("mae_event_p95", ".3f"),
                kbits_mean=_fmt_mean_std("kbits_mean", ".2f"),
            ))
        else:
            lines.append("| {profile} | {policy} | {sensor} | {n_events} | {duration_s} | {rate_Bps} | {aoi_mean_ms} | {aoi_p95_ms} | {mae_event_mean} | {mae_event_p95} | {kbits_mean} |".format(
                profile=r["profile"], policy=r["policy"], sensor=r["sensor"],
                n_events=int(r["n_events"]),
                duration_s=fmt["duration_s"](r["duration_s"]) if np.isfinite(r["duration_s"]) else "NaN",
                rate_Bps=fmt["rate_Bps"](r["rate_Bps"]) if np.isfinite(r["rate_Bps"]) else "NaN",
                aoi_mean_ms=fmt["aoi_mean_ms"](r["aoi_mean_ms"]) if np.isfinite(r["aoi_mean_ms"]) else "NaN",
                aoi_p95_ms=fmt["aoi_p95_ms"](r["aoi_p95_ms"]) if np.isfinite(r["aoi_p95_ms"]) else "NaN",
                mae_event_mean=fmt["mae_event_mean"](r["mae_event_mean"]) if np.isfinite(r["mae_event_mean"]) else "NaN",
                mae_event_p95=fmt["mae_event_p95"](r["mae_event_p95"]) if np.isfinite(r["mae_event_p95"]) else "NaN",
                kbits_mean=fmt["kbits_mean"](r["kbits_mean"]) if np.isfinite(r["kbits_mean"]) else "NaN",
            ))
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
    lines.append("개선율(%) 정의(낮을수록 좋은 지표 기준): `improvement = (baseline - candidate) / baseline * 100`")
    lines.append("")
    lines.append("| profile | sensor | policy | ΔRate[B/s] | Rate 개선율[%] | ΔAoIμ[ms] | AoIμ 개선율[%] | ΔMAE | MAE 개선율[%] |")
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
        lines.append(
            "| {profile} | {sensor} | {policy} | {d_rate} | {p_rate} | {d_aoi} | {p_aoi} | {d_mae} | {p_mae} |".format(
                profile=r["profile"],
                sensor=r["sensor"],
                policy=r["policy"],
                d_rate=_num(float(r.get("rate_Bps_delta_Bps", float("nan"))), "+.1f"),
                p_rate=_pct(float(r.get("rate_Bps_improvement_pct", float("nan")))),
                d_aoi=_num(float(r.get("aoi_mean_ms_delta_ms", float("nan"))), "+.1f"),
                p_aoi=_pct(float(r.get("aoi_mean_ms_improvement_pct", float("nan")))),
                d_mae=_num(float(r.get("mae_event_mean_delta_mae", float("nan"))), "+.3f"),
                p_mae=_pct(float(r.get("mae_event_mean_improvement_pct", float("nan")))),
            )
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
            for rel in [
                f"{figures_dir}/bar_rate_Bps__{prof_s}__{sensor_s}.png",
                f"{figures_dir}/bar_aoi_p95_ms__{prof_s}__{sensor_s}.png",
                f"{figures_dir}/bar_mae_event_p95__{prof_s}__{sensor_s}.png",
                f"{figures_dir}/pareto_rate_Bps_vs_aoi_mean_ms__{prof_s}__{sensor_s}.png",
            ]:
                if (out_dir / rel).exists():
                    lines.append(f"![]({rel})")
            lines.append("")

    p.write_text("\n".join(lines), encoding="utf-8")


def _try_make_plots(out_dir: Path, summary: pd.DataFrame) -> list[Path]:
    """
    시각화(막대/파레토) 이미지를 생성한다.
    - matplotlib이 없으면 조용히 skip 한다.
    - 반환: 생성된 이미지 경로 목록
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        return []

    figs_dir = out_dir / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)

    policy_order = ["periodic", "fixed_tau", "adaptive"]
    colors = {"periodic": "#9CA3AF", "fixed_tau": "#2563EB", "adaptive": "#F59E0B"}
    created: list[Path] = []

    def _slug(x: str) -> str:
        return (
            str(x)
            .replace("/", "_")
            .replace("\\", "_")
            .replace(" ", "_")
            .replace(":", "_")
        )

    def _bar_one(
        g: pd.DataFrame,
        *,
        metric: str,
        ylabel: str,
        title: str,
        out_name: str,
    ) -> Path:
        g = g.copy()
        g["policy"] = pd.Categorical(g["policy"], categories=policy_order, ordered=True)
        g = g.sort_values("policy")
        xs = [str(x) for x in g["policy"].tolist()]
        ys = [float(y) for y in g[metric].tolist()]
        err_col = f"{metric}_std"
        yerr = None
        if err_col in g.columns:
            errs = [float(e) if np.isfinite(e) else 0.0 for e in g[err_col].tolist()]
            yerr = errs

        fig, ax = plt.subplots(figsize=(6.2, 3.4))
        ax.bar(
            xs,
            ys,
            yerr=yerr,
            capsize=4,
            color=[colors.get(x, "#6B7280") for x in xs],
        )
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", alpha=0.25)
        fig.tight_layout()
        out_path = figs_dir / _slug(out_name)
        fig.savefig(out_path, dpi=170)
        plt.close(fig)
        return out_path

    def _pareto_one(
        g: pd.DataFrame,
        *,
        x: str,
        y: str,
        title: str,
        ylabel: str,
        out_name: str,
    ) -> Path:
        g = g.copy()
        g["policy"] = pd.Categorical(g["policy"], categories=policy_order, ordered=True)
        g = g.sort_values("policy")
        fig, ax = plt.subplots(figsize=(6.0, 3.8))
        for _, r in g.iterrows():
            pol = str(r["policy"])
            ax.scatter(
                float(r[x]),
                float(r[y]),
                s=85,
                color=colors.get(pol, "#6B7280"),
                label=pol,
            )
            ax.annotate(pol, (float(r[x]), float(r[y])), textcoords="offset points", xytext=(6, 6))
        ax.set_title(title)
        ax.set_xlabel("Rate [B/s] (lower is better)")
        ax.set_ylabel(f"{ylabel} (lower is better)")
        ax.grid(alpha=0.25)
        # legend 중복 제거
        handles, labels = ax.get_legend_handles_labels()
        uniq = dict(zip(labels, handles))
        ax.legend(uniq.values(), uniq.keys(), loc="best", frameon=True)
        fig.tight_layout()
        out_path = figs_dir / _slug(out_name)
        fig.savefig(out_path, dpi=170)
        plt.close(fig)
        return out_path

    # profile × sensor별로 주요 지표 bar + pareto 생성
    for (prof, sensor), g in summary.groupby(["profile", "sensor"], sort=False):
        prof_s = str(prof)
        sensor_s = str(sensor)
        title_base = f"{prof_s} / {sensor_s}"

        created.append(
            _bar_one(
                g,
                metric="rate_Bps",
                ylabel="Rate [B/s]",
                title=f"{title_base} · Rate",
                out_name=f"bar_rate_Bps__{prof_s}__{sensor_s}.png",
            )
        )
        created.append(
            _bar_one(
                g,
                metric="aoi_mean_ms",
                ylabel="AoI mean [ms]",
                title=f"{title_base} · AoI mean",
                out_name=f"bar_aoi_mean_ms__{prof_s}__{sensor_s}.png",
            )
        )
        created.append(
            _bar_one(
                g,
                metric="aoi_p95_ms",
                ylabel="AoI p95 [ms]",
                title=f"{title_base} · AoI p95",
                out_name=f"bar_aoi_p95_ms__{prof_s}__{sensor_s}.png",
            )
        )
        created.append(
            _bar_one(
                g,
                metric="mae_event_mean",
                ylabel="MAE mean",
                title=f"{title_base} · MAE mean",
                out_name=f"bar_mae_event_mean__{prof_s}__{sensor_s}.png",
            )
        )
        created.append(
            _bar_one(
                g,
                metric="mae_event_p95",
                ylabel="MAE p95",
                title=f"{title_base} · MAE p95",
                out_name=f"bar_mae_event_p95__{prof_s}__{sensor_s}.png",
            )
        )
        created.append(
            _bar_one(
                g,
                metric="kbits_mean",
                ylabel="k̄ (mean quantization bits)",
                title=f"{title_base} · k̄",
                out_name=f"bar_kbits_mean__{prof_s}__{sensor_s}.png",
            )
        )
        created.append(
            _pareto_one(
                g,
                x="rate_Bps",
                y="aoi_mean_ms",
                ylabel="AoI mean [ms]",
                title=f"{title_base} · Pareto (Rate vs AoI mean)",
                out_name=f"pareto_rate_Bps_vs_aoi_mean_ms__{prof_s}__{sensor_s}.png",
            )
        )

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
            "- `{}/{}` sensor={} · events={} · rate={} B/s · AoIμ={} ms (p95={} ms) · MAE={} (p95={}) · k̄={}".format(
                profile,
                policy,
                sensor,
                events,
                rate,
                aoi_mean,
                aoi_p95,
                mae_mean,
                mae_p95,
                kbits,
            )
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
    ap.add_argument("--no-plots", action="store_true",
                    help="시각화(figures/) 생성 생략")
    ap.add_argument("--discord-webhook", default=None,
                    help="요약 결과를 전송할 Discord webhook URL")
    ap.add_argument("--discord-username", default=None,
                    help="Discord 메시지에 사용할 표시 이름(옵션)")
    ap.add_argument("--discord-mention", action="append", default=[],
                    help="메시지에서 멘션할 Discord 사용자 ID (여러 번 지정 가능)")
    return ap.parse_args()


def main():
    args = parse_args()
    out_dir = Path(args.out)

    df = load_events(args.input)
    df = dedup_and_sort(df)
    by_run = summarize_by_run(df)
    summary = summarize(by_run)
    baseline_policy = str(args.baseline_policy)
    comparisons = compare_policies(summary, baseline_policy=baseline_policy)

    out_dir.mkdir(parents=True, exist_ok=True)
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

    figures = [] if args.no_plots else _try_make_plots(out_dir, summary)
    _write_report_md(out_dir, summary, comparisons=comparisons, baseline_policy=baseline_policy)

    print(f"[analyze] rows={len(df)} scenarios={summary[['profile','policy','sensor']].drop_duplicates().shape[0]}")
    print(f"[analyze] saved: {csv_path}")
    print(f"[analyze] saved: {by_run_path}")
    print(f"[analyze] saved: {cmp_path}")
    if args.save_parquet:
        print(f"[analyze] saved: {pq_path}")
    if figures:
        print(f"[analyze] figures: {len(figures)} files under {out_dir / 'figures'}")

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
            print("[analyze] sent Discord notification")
        except DiscordWebhookError as e:
            print(f"[analyze] WARN: failed to send Discord notification: {e}")


if __name__ == "__main__":
    main()
