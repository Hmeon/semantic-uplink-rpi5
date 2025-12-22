# RPi5 profiling report

## Methodology
- Benchmark script: `scripts/bench_policy_rpi5.py`
- Host run (Windows reference): `.\.venv_audit\Scripts\python scripts\bench_policy_rpi5.py --steps 2000 --out artifacts\bench_policy_rpi5.csv`
- RPi5 run (recommended):
  - `python -m venv .venv && source .venv/bin/activate`
  - `python scripts/bench_policy_rpi5.py --steps 2000 --out artifacts/bench_policy_rpi5.csv`
- Notes: timings below are from a Windows host; re-run on RPi5 to validate real hardware timings and `maxrss`.

## Timing summary (p50/p95)
| metric | p50_ms | p95_ms | mean_ms | n | cpu_total_ms | maxrss_kb |
|---|---:|---:|---:|---:|---:|---:|
| t_predict_ms | 0.0163 | 0.0381 | 0.0197 | 2000 | 421.88 | nan |
| t_decide_ms | 0.0977 | 0.1812 | 0.1140 | 2000 | 421.88 | nan |
| t_observe_ms | 0.0170 | 0.0396 | 0.0207 | 2000 | 421.88 | nan |
| t_step_ms | 0.1662 | 0.2896 | 0.1870 | 2000 | 421.88 | nan |
| cpu_step_ms | 0.0000 | 0.0000 | 0.1719 | 2000 | 421.88 | nan |

## Interpretation
- `t_decide_ms` and `t_step_ms` are below 1 ms on this host; RPi5 should be validated with the same script.
- `maxrss_kb` is `nan` on Windows because `resource.getrusage` is unavailable; expect real values on Linux/RPi5.
- Use these numbers as a baseline; repeat with higher arm counts and multiple sensors to capture scaling effects.

## Scalability considerations
- Multi-sensor runs scale linearly with per-sensor policy loops; confirm p95 under target thresholds at expected sensor counts.
- Increase `--steps` and synthetic rates to mimic 10-20 Hz for MIC and 1 Hz for TEMP.
- Watch outbox size and `rate_limit_skips` when min_emit_interval_ms is low.
