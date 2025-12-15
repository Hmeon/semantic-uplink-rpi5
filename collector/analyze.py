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
import math
import os
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from common.config import load_policy_config_dict
from common.discord_webhook import DiscordWebhookError, send_discord_message
from common.metrics import percent_improvement
from common.schema import EventMsg, LinkProfile, PolicyMode

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
    }
    for k, t in cast_cols.items():
        if k in out.columns:
            out[k] = out[k].astype(t)
    out["run_id"] = out.get("run_id", out["__source_file"]).astype("string")
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
        rx_delay_p95_ms = float("nan")
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
            "rx_delay_p95_ms": float(rx_delay_p95_ms),
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
    for c in ["event_rate_hz", "send_ratio", "rx_delay_mean_ms", "rx_delay_p95_ms"]:
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
    figures_dir: str = "figures",
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

        # paper-plots(추가): 논문/최종보고서용 플롯이 있으면 함께 임베드
        paper_any = any(figs_path.glob("paper_*.png"))
        if paper_any:
            lines.append("---")
            lines.append("")
            lines.append("## Paper Figures (논문용 추가 플롯)")
            lines.append("")

            # sensor 단위 환경 비교(Reward/최종지표)
            for sensor_s in sorted({str(s) for s in summary["sensor"].unique()}):
                for rel in [
                    f"{figures_dir}/paper_env_metrics__{sensor_s}.png",
                    f"{figures_dir}/paper_env_reward_over_time__{sensor_s}.png",
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

                rel = f"{figures_dir}/paper_action_heatmap__{prof_s}__{sensor_s}.png"
                if (out_dir / rel).exists():
                    lines.append(f"![]({rel})")

                for pattern in [
                    f"paper_feature_weights__*__{prof_s}__{sensor_s}.png",
                    f"paper_reward_over_time__*__{prof_s}__{sensor_s}.png",
                    f"paper_cumulative_regret__*__{prof_s}__{sensor_s}.png",
                    f"paper_stability_abs_res__*__{prof_s}__{sensor_s}.png",
                    f"paper_timeline__*__{prof_s}__{sensor_s}.png",
                ]:
                    matches = sorted(figs_path.glob(pattern))
                    if matches:
                        lines.append(f"![]({figures_dir}/{matches[0].name})")
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
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore
    except Exception:
        return []

    figs_dir = out_dir / "figures"
    figs_dir.mkdir(parents=True, exist_ok=True)

    def _slug(x: str) -> str:
        return str(x).replace("/", "_").replace("\\", "_").replace(" ", "_").replace(":", "_")

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
            axes[0].set_title(f"Policy comparison by profile · sensor={sensor}")
            axes[0].legend(loc="best", frameon=True)
            fig.tight_layout()
            out_path = figs_dir / _slug(f"paper_env_metrics__{sensor}.png")
            fig.savefig(out_path, dpi=180)
            plt.close(fig)
            created.append(out_path)

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
                out_path = figs_dir / _slug(f"paper_action_heatmap__{prof}__{sensor}.png")
                fig.savefig(out_path, dpi=180)
                plt.close(fig)
                created.append(out_path)

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
        out_path = figs_dir / _slug(f"paper_env_reward_over_time__{sensor}.png")
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        created.append(out_path)

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
        out_path = figs_dir / _slug(f"paper_feature_weights__{rep_run}__{prof}__{sensor}.png")
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        created.append(out_path)

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
        out_path = figs_dir / _slug(f"paper_reward_over_time__{rep_run}__{prof}__{sensor}.png")
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        created.append(out_path)

        # 3-3) Cumulative regret (proxy)
        fig, ax = plt.subplots(figsize=(9.8, 4.2))
        ax.plot(trace["step"], trace["regret_pred_cum"], color="#111827", linewidth=2.2)
        ax.set_title(f"Cumulative Regret (predicted proxy) · {rep_run} · {prof}/{sensor}")
        ax.set_xlabel("decision step")
        ax.set_ylabel("Cumulative Regret")
        ax.grid(alpha=0.25)
        fig.tight_layout()
        out_path = figs_dir / _slug(f"paper_cumulative_regret__{rep_run}__{prof}__{sensor}.png")
        fig.savefig(out_path, dpi=180)
        plt.close(fig)
        created.append(out_path)

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
            out_path = figs_dir / _slug(f"paper_stability_abs_res__{rep_run}__{prof}__{sensor}.png")
            fig.savefig(out_path, dpi=180)
            plt.close(fig)
            created.append(out_path)

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
            out_path = figs_dir / _slug(f"paper_timeline__{rep_run}__{prof}__{sensor}.png")
            fig.savefig(out_path, dpi=180)
            plt.close(fig)
            created.append(out_path)

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
    ap.add_argument("--no-plots", action="store_true",
                    help="시각화(figures/) 생성 생략")
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
    paper_figures: list[Path] = []
    if bool(args.paper_plots):
        try:
            decisions = load_decisions(args.input)
        except Exception as e:
            print(f"[analyze] WARN: failed to load decisions logs: {e}")
            decisions = pd.DataFrame()
        try:
            paper_figures = _try_make_paper_plots(
                out_dir,
                events=df,
                decisions=decisions,
                summary=summary,
                policy_config_path=str(args.policy_config),
                reward_window=int(args.reward_window),
                action_bins=int(args.action_bins),
                top_actions=int(args.top_actions),
                cellular_var_period_s=int(args.cellular_var_period_s),
            )
        except Exception as e:
            print(f"[analyze] WARN: failed to generate paper plots: {e}")
            paper_figures = []

    _write_report_md(out_dir, summary, comparisons=comparisons, baseline_policy=baseline_policy)

    scenarios = summary[["profile", "policy", "sensor"]].drop_duplicates().shape[0]
    print(f"[analyze] rows={len(df)} scenarios={scenarios}")
    print(f"[analyze] saved: {csv_path}")
    print(f"[analyze] saved: {by_run_path}")
    print(f"[analyze] saved: {cmp_path}")
    if args.save_parquet:
        print(f"[analyze] saved: {pq_path}")
    if figures:
        print(f"[analyze] figures: {len(figures)} files under {out_dir / 'figures'}")
    if paper_figures:
        print(f"[analyze] paper figures: {len(paper_figures)} files under {out_dir / 'figures'}")

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
