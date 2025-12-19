from __future__ import annotations

import argparse
import csv
import math
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.schema import LinkProfile, PolicyMode, SensorType
from edge.policy.linucb import Arm, LinUCBConfig
from edge.policy.runtime import SensorPolicyRuntime
from edge.predict.ewma import EWMAConfig


class _TempSample:
    def __init__(self, ts_ns: int, seq: int, celsius: float, valid: bool = True) -> None:
        self.ts_ns = ts_ns
        self.seq = seq
        self.celsius = celsius
        self.valid = valid


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    idx = int(round((pct / 100.0) * (len(xs) - 1)))
    idx = max(0, min(idx, len(xs) - 1))
    return float(xs[idx])


def _mean(values: list[float]) -> float:
    if not values:
        return float("nan")
    return float(sum(values) / len(values))


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark EWMA + LinUCB runtime timings.")
    ap.add_argument("--steps", type=int, default=2000, help="number of steps to run")
    ap.add_argument(
        "--out",
        default="artifacts/bench_policy_rpi5.csv",
        help="output CSV path",
    )
    args = ap.parse_args()

    ewma_cfg = EWMAConfig(
        device_id="bench",
        sensor=SensorType.TEMP,
        alpha=0.5,
        tau=0.0,
        kbits=8,
        profile=LinkProfile.SLOW_10KBPS,
        heartbeat_s=None,
        min_emit_interval_ms=0,
        bootstrap_emit=True,
        diagnostics_enabled=True,
    )
    linucb_cfg = LinUCBConfig(
        device_id="bench",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        arms=[Arm(0.0, 8), Arm(0.0, 10)],
        aoi_max_ms=10_000.0,
        mae_max=1e6,
        warmup_per_arm=1,
        diagnostics_enabled=True,
    )
    rt = SensorPolicyRuntime(
        device_id="bench",
        sensor=SensorType.TEMP,
        profile=LinkProfile.SLOW_10KBPS,
        mode=PolicyMode.ADAPTIVE,
        ewma_cfg=ewma_cfg,
        linucb_cfg=linucb_cfg,
        nominal_period_s=1.0,
    )

    t_predict: list[float] = []
    t_decide: list[float] = []
    t_observe: list[float] = []
    t_step: list[float] = []
    cpu_step: list[float] = []
    maxrss: list[float] = []

    cpu_start_ns = time.process_time_ns()
    t0_ns = time.time_ns()
    for i in range(int(args.steps)):
        ts_ns = t0_ns + i * 100_000_000  # 10 Hz synthetic
        value = float(i % 2)
        sample = _TempSample(ts_ns=ts_ns, seq=i, celsius=value)
        res = rt.step(sample, outbox_pending=0)
        if res.decision is None:
            continue
        dec = res.decision
        if dec.t_predict_ms is not None:
            t_predict.append(float(dec.t_predict_ms))
        if dec.t_decide_ms is not None:
            t_decide.append(float(dec.t_decide_ms))
        if dec.t_observe_ms is not None:
            t_observe.append(float(dec.t_observe_ms))
        if dec.t_step_ms is not None:
            t_step.append(float(dec.t_step_ms))
        if dec.cpu_step_ms is not None:
            cpu_step.append(float(dec.cpu_step_ms))
        if dec.maxrss_kb is not None:
            maxrss.append(float(dec.maxrss_kb))
    cpu_total_ms = (time.process_time_ns() - cpu_start_ns) / 1e6

    out_path = Path(str(args.out))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("t_predict_ms", t_predict),
        ("t_decide_ms", t_decide),
        ("t_observe_ms", t_observe),
        ("t_step_ms", t_step),
        ("cpu_step_ms", cpu_step),
    ]
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "p50_ms", "p95_ms", "mean_ms", "n", "cpu_total_ms", "maxrss_kb"])
        maxrss_kb = max(maxrss) if maxrss else float("nan")
        for name, values in rows:
            p50 = _percentile(values, 50.0)
            p95 = _percentile(values, 95.0)
            mean = _mean(values)
            n = len(values)
            w.writerow([name, f"{p50:.4f}", f"{p95:.4f}", f"{mean:.4f}", n, f"{cpu_total_ms:.2f}", f"{maxrss_kb:.1f}"])

    print(f"saved: {out_path} (steps={args.steps})")


if __name__ == "__main__":
    main()
