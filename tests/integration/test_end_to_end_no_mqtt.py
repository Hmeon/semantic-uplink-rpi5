from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import pandas as pd

from collector import analyze
from collector.collector import Collector, Config
from common.schema import EventMsg, LinkProfile, PolicyDecisionMsg, PolicyMode, SensorType


def test_end_to_end_without_mqtt_broker(tmp_path: Path, monkeypatch) -> None:
    """
    Docker/Mosquitto 없이도 '수집 → 중복제거 → Parquet → 분석' 경로를 검증한다.

    - Collector는 ingest_message()로 이벤트를 주입하고, flush_once()로 Parquet를 생성한다.
    - analyze는 동일 run_dir에서 파일을 탐색/정규화해 요약/비교 테이블을 만든다.
    """
    run_dir = tmp_path / "run1"
    out_dir = tmp_path / "analysis"
    run_dir.mkdir(parents=True, exist_ok=True)

    cfg = Config(run_dir=str(run_dir), broker="unused", port=0, flush_interval_s=3600)
    collector = Collector(cfg)

    device_id = "dev1"
    profile = LinkProfile.SLOW_10KBPS
    sensor = SensorType.TEMP
    kbits = 8
    base_ts = time.time_ns()

    def _ev(seq: int, policy: PolicyMode, tau: float) -> EventMsg:
        val = float(seq) * 0.1
        pred = val - 0.05
        res = val - pred
        return EventMsg(
            ts=int(base_ts + (seq * 50_000_000)),
            seq=int(seq),
            device_id=device_id,
            sensor=sensor,
            val=val,
            pred=pred,
            res=res,
            tau=float(tau),
            kbits=int(kbits),
            profile=profile,
            policy=policy,
        )

    # 1) unique events
    events_unique: list[EventMsg] = []
    for seq in range(1, 6):
        events_unique.append(_ev(seq, PolicyMode.PERIODIC, tau=-1e-9))
    for seq in range(6, 11):
        events_unique.append(_ev(seq, PolicyMode.FIXED_TAU, tau=0.2))

    # Add one policy decision + one marker to exercise non-event sinks.
    decision = PolicyDecisionMsg(
        ts=int(base_ts + 1),
        device_id=device_id,
        state_aoi=0.0,
        state_res=0.0,
        state_res_var=0.0,
        state_loss=0.0,
        state_q_len=0,
        tau=0.2,
        kbits=kbits,
        reward=0.0,
    )
    collector.ingest_message(
        topic=decision.mqtt_topic(),
        payload=decision.to_json_bytes(),
        qos=1,
        dup=False,
        retain=False,
        t_recv_ns=int(base_ts + 1_000_000),
    )
    collector.ingest_message(
        topic=f"marker/{device_id}",
        payload=json.dumps(
            {"ts": int(base_ts + 2), "device_id": device_id, "note": "test_marker"}
        ).encode("utf-8"),
        qos=1,
        dup=False,
        retain=False,
        t_recv_ns=int(base_ts + 2_000_000),
    )

    for ev in events_unique:
        collector.ingest_message(
            topic=ev.mqtt_topic(),
            payload=ev.to_json_bytes(),
            qos=1,
            dup=False,
            retain=False,
            t_recv_ns=int(ev.ts + 10_000_000),
        )

    collector.flush_once()

    # 2) duplicates across flush boundary (should be dropped)
    dup_events = [
        _ev(2, PolicyMode.PERIODIC, tau=-1e-9),
        _ev(7, PolicyMode.FIXED_TAU, tau=0.2),
    ]
    for ev in dup_events:
        collector.ingest_message(
            topic=ev.mqtt_topic(),
            payload=ev.to_json_bytes(),
            qos=1,
            dup=True,
            retain=False,
            t_recv_ns=int(ev.ts + 20_000_000),
        )

    collector.flush_once()

    logs_dir = run_dir / "logs"
    meta_path = logs_dir / "collector_meta.json"
    assert meta_path.exists()

    events_paths = sorted(logs_dir.glob("events_*.parquet"))
    assert events_paths
    df_events = pd.concat([pd.read_parquet(p) for p in events_paths], ignore_index=True)
    assert len(df_events) == 10  # unique seq only

    decisions_paths = sorted(logs_dir.glob("decisions_*.parquet"))
    assert decisions_paths
    df_decisions = pd.concat([pd.read_parquet(p) for p in decisions_paths], ignore_index=True)
    assert len(df_decisions) == 1

    markers_paths = sorted(logs_dir.glob("markers_*.parquet"))
    assert markers_paths
    df_markers = pd.concat([pd.read_parquet(p) for p in markers_paths], ignore_index=True)
    assert len(df_markers) == 1

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert int(meta["events_unique"]) == 10
    assert int(meta["dup_messages_dropped"]) >= 2

    # 3) analyze in-process (no subprocess so coverage counts)
    events = analyze.load_events([run_dir])
    assert set(events["policy"].unique()) >= {"periodic", "fixed_tau"}

    events = analyze.dedup_and_sort(events)
    by_run = analyze.summarize_by_run(events)
    summary = analyze.summarize(by_run)
    comparisons = analyze.compare_policies(summary, baseline_policy="periodic")

    out_dir.mkdir(parents=True, exist_ok=True)
    summary.to_csv(out_dir / "metrics_summary.csv", index=False)
    comparisons.to_csv(out_dir / "metrics_vs_periodic.csv", index=False)

    # periodic baseline must exist → fixed_tau row improvements are finite (not NaN).
    fixed = comparisons[
        (comparisons["profile"] == profile.value)
        & (comparisons["sensor"] == sensor.value)
        & (comparisons["policy"] == PolicyMode.FIXED_TAU.value)
    ]
    assert len(fixed) == 1
    row = fixed.iloc[0]
    assert not math.isnan(float(row["baseline_rate_Bps"]))
    assert not math.isnan(float(row["rate_Bps_improvement_pct"]))

    # 4) non-Docker integration equivalence: exercise analyze.main artifact contract
    # on the same run directory, without MQTT broker or subprocess orchestration.
    out_dir_main = tmp_path / "analysis_main"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(run_dir),
            "--out",
            str(out_dir_main),
            "--baseline-policy",
            "periodic",
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--no-audit",
            "--log-level",
            "ERROR",
        ],
    )
    analyze.main()

    for name in [
        "analysis_meta.json",
        "metrics_summary.csv",
        "metrics_by_run.csv",
        "metrics_vs_periodic.csv",
        "metrics_vs_fixed_tau.csv",
        "kpi_final.csv",
        "kpi_verdict.json",
        "report.md",
    ]:
        assert (out_dir_main / name).exists()

    verdict = json.loads((out_dir_main / "kpi_verdict.json").read_text(encoding="utf-8"))
    # This non-Docker run injects periodic/fixed_tau only, so KPI is not applicable.
    assert verdict.get("project_verdict") == "SKIP"

    # 5) non-Docker integration equivalence: audit-enabled analyzer path.
    out_dir_main_audit = tmp_path / "analysis_main_audit"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "analyze.py",
            "--input",
            str(run_dir),
            "--out",
            str(out_dir_main_audit),
            "--baseline-policy",
            "periodic",
            "--no-plots",
            "--no-paper-plots",
            "--no-diagnostic-plots",
            "--audit",
            "--log-level",
            "ERROR",
        ],
    )
    analyze.main()

    assert (out_dir_main_audit / "quality_audit.json").exists()
    assert (out_dir_main_audit / "quality_audit.md").exists()
    verdict_audit = json.loads(
        (out_dir_main_audit / "kpi_verdict.json").read_text(encoding="utf-8")
    )
    assert verdict_audit.get("project_verdict") == "SKIP"
