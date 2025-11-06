# collector/analyze.py
# Python 3.10+
# 목적: 수집된 Event 로그를 읽어 Rate/AoI/MAE를 시나리오별로 집계하고,
#       표/파레토 분석에 필요한 요약 CSV/Parquet/Markdown을 생성한다.
#
# 입력 기대:
#  - Parquet: *.parquet (권장, 빠름)
#  - CSV    : *.csv (utf-8, 헤더 포함)
#  - 파일/디렉터리 모두 허용. 디렉터리는 재귀 검색하며 events.(parquet|csv) 우선.
#  - 필수 컬럼(스키마 정합): ts, seq, device_id, sensor, val, pred, res, tau, kbits, profile, policy
#
# 지표 정의:
#  - Rate(브로커 수신 바이트/초) = Σ MQTT_PUBLISH_추정바이트 / 관측시간[s]
#      → payload는 EventMsg JSON 직렬화, 헤더 포함(v3.1.1). (common.mqttutil 기반)
#  - AoI 평균/95번째백분위(ms): 이벤트 간 간격 Δ_i로부터 *연속시간 분포*를 정확 계산
#      평균 = Σ Δ_i^2 / (2 Σ Δ_i),  P95 = a* s.t. Σ min(a*, Δ_i) = 0.95 Σ Δ_i
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
            df = pd.read_parquet(f)
        elif f.suffix.lower() == ".csv":
            df = pd.read_csv(f)
        else:
            continue
        # 최소 컬럼 확인
        required = {"ts", "seq", "device_id", "sensor", "val", "pred", "res", "tau", "kbits", "profile", "policy"}
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
        dfs.append(df)

    if not dfs:
        raise RuntimeError("no readable event files")
    out = pd.concat(dfs, ignore_index=True)
    # 타입 캐스팅(안전)
    cast_cols = {
        "ts": "int64", "seq": "uint64",
        "device_id": "string", "sensor": "string",
        "val": "float64", "pred": "float64", "res": "float64",
        "tau": "float64", "kbits": "int64",
        "profile": "string", "policy": "string",
    }
    for k, t in cast_cols.items():
        if k in out.columns:
            out[k] = out[k].astype(t)
    return out


def _infer_profile_policy_from_path(p: Path) -> tuple[str, str]:
    """
    실험 러너(run_scenarios)가 만든 폴더명 '<profile>__<mode>'에서 추론.
    실패 시 안전값으로 ('unknown','unknown') 반환.
    """
    try:
        name = p.parent.name
        if "__" in name:
            prof, mode = name.split("__", 1)
            # 유효성(열거형)
            prof = LinkProfile(prof).value
            mode = PolicyMode(mode).value
            return prof, mode
    except Exception:
        pass
    return "unknown", "unknown"


# ------------------------- 전처리/유틸 -------------------------

def dedup_and_sort(df: pd.DataFrame) -> pd.DataFrame:
    """
    (device_id, sensor, seq) 기준 QoS1 중복 제거, ts 오름차순 정렬.
    """
    key = ["device_id", "sensor", "seq"]
    if not set(key).issubset(df.columns):
        raise ValueError("dedup requires columns: device_id, sensor, seq")
    # 첫 등장을 유지(또는 ts가 가장 이른 것)
    df = df.sort_values(["device_id", "sensor", "seq", "ts"], kind="mergesort")
    df = df.drop_duplicates(subset=key, keep="first", ignore_index=True)
    # 전역 시간 정렬(그룹 내 분석 시에는 다시 묶음)
    df = df.sort_values(["device_id", "sensor", "ts"], kind="mergesort").reset_index(drop=True)
    return df


def estimate_payload_bytes(df: pd.DataFrame) -> pd.Series:
    """
    EventMsg를 재구성하여 MQTT v3.1.1 PUBLISH 바이트(헤더 포함)를 추정.
    df에 'mqtt_bytes' 컬럼이 이미 있다면 그대로 사용.
    """
    if "mqtt_bytes" in df.columns:
        s = df["mqtt_bytes"].astype("int64")
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
    total = float(np.sum(deltas_ms))
    if total <= 0:
        return float("nan"), float("nan")
    mean_ms = float(np.sum(deltas_ms ** 2) / (2.0 * total))
    # P95: 길이 가중 Uniform 혼합의 분위수
    # 조건: sum(min(a, Δ_i)) = p * sum(Δ_i)
    p = 0.95
    target = p * total
    # 오름차순 정렬 후 누적 길이로 해 찾기
    d_sorted = np.sort(deltas_ms)
    # 누적: 아직 a<Δ_i 영역에서는 a가 동일하게 더해짐 → 구간별로 해 닫힘 형태
    # i개 구간을 넘으면 sum(min(a,Δ)) = a*i + sum_{j>i} Δ_j
    # a = (target - sum_tail) / i  를 만족하는 첫 i를 찾는다.
    tail_cumsum = np.cumsum(d_sorted[::-1])[::-1]  # 각 인덱스부터의 꼬리합
    n = len(d_sorted)
    for i in range(1, n + 1):
        sum_tail = tail_cumsum[n - i] if (n - i) < n else 0.0
        a = (target - sum_tail) / i
        if i == n or a <= d_sorted[i]:
            # a는 [d_sorted[i-1], d_sorted[i]] 사이
            a = max(a, 0.0)
            return float(mean_ms), float(a)
    # 폴백(수치 노이즈)
    return float(mean_ms), float(d_sorted[-1])


# ------------------------- 집계 로직 -------------------------

def summarize(df: pd.DataFrame) -> pd.DataFrame:
    """
    profile × policy × sensor 단위로 Rate/AoI/MAE를 집계.
    반환: 멀티 러너 합친 단일 테이블.
    """
    need = {"ts", "device_id", "sensor", "seq", "profile", "policy", "res", "kbits"}
    if not need.issubset(df.columns):
        missing = sorted(need - set(df.columns))
        raise ValueError(f"missing columns for summarize: {missing}")

    # 중복 제거/정렬
    df = dedup_and_sort(df).copy()
    # MQTT 바이트 추정
    df["mqtt_bytes"] = estimate_payload_bytes(df)

    # 그룹키
    keys = ["profile", "policy", "sensor"]

    rows = []
    for (prof, pol, sensor), g in df.groupby(keys, sort=False):
        ts = g["ts"].astype("int64").to_numpy()
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

        # 이벤트 기반 MAE 통계(잔차 res)
        mae_mean = float(g["res"].abs().mean())
        mae_p95 = float(g["res"].abs().quantile(0.95)) if len(g) > 0 else np.nan

        rows.append({
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
        })

    out = pd.DataFrame(rows)
    # 정렬: profile, policy(고정 순서), sensor
    pol_order = ["periodic", "fixed_tau", "adaptive"]
    out["policy"] = pd.Categorical(out["policy"], categories=pol_order, ordered=True)
    out = out.sort_values(["profile", "policy", "sensor"]).reset_index(drop=True)
    return out


# --------------------------- 리포트 ---------------------------

def _write_report_md(out_dir: Path, summary: pd.DataFrame) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "report.md"
    lines = []
    lines.append("# 실험 요약 리포트")
    lines.append("")
    lines.append("본 리포트는 **이벤트 기반 MAE(res)**를 사용합니다. 전체 시계열 MAE가 필요하면 원시 스트림 또는 복원 파이프가 추가로 필요합니다.")
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
    lines.append("| profile | policy | sensor | n_events | dur[s] | rate[B/s] | AoI_mean[ms] | AoI_p95[ms] | MAE_event_mean | MAE_event_p95 | k̄ |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in tbl.iterrows():
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
    summary = summarize(df)

    out_dir.mkdir(parents=True, exist_ok=True)
    # 저장
    csv_path = out_dir / "metrics_summary.csv"
    summary.to_csv(csv_path, index=False)
    if args.save_parquet:
        try:
            pq_path = out_dir / "metrics_summary.parquet"
            summary.to_parquet(pq_path, index=False)
        except Exception:
            pass

    _write_report_md(out_dir, summary)

    print(f"[analyze] rows={len(df)} scenarios={summary[['profile','policy','sensor']].drop_duplicates().shape[0]}")
    print(f"[analyze] saved: {csv_path}")
    if args.save_parquet:
        print(f"[analyze] saved: {pq_path}")

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
