# Final Evaluation (slow_10kbps, 3h)

This report summarizes the final comparison and notes follow-up improvements
if we want to push receiver-side freshness further, plus the exact CLI options
and the synchronization rules used to keep scale/link conditions consistent across policies.

## 1) Data Sources and Outputs
- Inputs:
  - `artifacts/slow10_periodic_3h_B/logs`
  - `artifacts/slow10_fixed_3h_B/logs`
  - `artifacts/slow10_linucb_3h_B/logs`
- Analysis output: `results/final_compare_3h_slow_10kbps`
- Baseline: `periodic`
- AoI/Rate use `t_recv_ns` (receiver time). MAE is event-based (`res`).

## 2) Final Results (Quantitative)
Values are means from `results/final_compare_3h_slow_10kbps/report.md`.

mic_rms:
- periodic: 524.0 B/s, AoI 1536.8 ms, MAE 0.052
- fixed_tau: 26.0 B/s, AoI 6287.7 ms, MAE 0.052
- adaptive: 37.4 B/s, AoI 4745.2 ms, MAE 0.053

temp:
- periodic: 255.0 B/s, AoI 1802.9 ms, MAE 0.036
- fixed_tau: 23.7 B/s, AoI 6952.2 ms, MAE 0.036
- adaptive: 27.4 B/s, AoI 5756.9 ms, MAE 0.035

Goal check (from README):
- Rate reduction >= 60% vs periodic: PASS (89-95% reduction)
- MAE change <= 10% vs fixed_tau: PASS (mic +1.9%, temp -2.8%)
- AoI improvement >= 15% vs fixed_tau: PASS (~17-25% improvement)
- Rate increase <= 50% vs fixed_tau: PASS (mic +44%, temp +16%)

Trade-off (policy ranking):
- Adaptive > Fixed_tau for AoI while keeping rate low.
  - mic_rms AoI improves ~24.5% vs fixed_tau (6287.7 -> 4745.2 ms)
  - temp AoI improves ~17.2% vs fixed_tau (6952.2 -> 5756.9 ms)
- Fixed_tau still gives the lowest rate, but AoI is worst.

Conclusion: adaptive is the best overall trade-off and meets the revised success
criteria; AoI vs periodic remains worse, which is expected under the MAE-first
objective.

## 3) Why LinUCB Can Improve Further
Observed issue: AoI vs periodic remains worse even though adaptive improves AoI
vs fixed_tau. The current learning signal does not reflect receiver-side
freshness well.

Key limitations in code (see `edge/policy/runtime.py`):
- AoI in policy state is computed from last emit time on the edge, not from
  receiver-side freshness (`t_recv_ns`), so the reward is misaligned with the
  evaluation metric.
- `state_loss` is fixed at 0.0. No real link-loss or queue delay estimate is
  injected into the context, reducing adaptivity to link conditions.

Recommended fixes:
- Use ACK/outbox timing to approximate receiver-side AoI (or add a delay model
  to the reward). Feed that into LinUCB as `state_aoi`.
- Populate `state_loss` with an observable signal (retries, drop estimates,
  or ack timeout rate).
- Rebalance reward weights/scales in `configs/policy_adaptive_aiot.yaml` to
  strengthen AoI penalties when it drifts (raise `alpha`, reduce `gamma`).
- Tighten safety guardrails (`aoi_max_ms`) or force a lower-tau arm on AoI
  violations to keep freshness under control.

## 4) Terminal A/B Commands (same conditions as the logs)
아래는 `slow_10kbps` 3시간 로그를 실측으로 재현하기 위한 동일 조건 옵션이다.
각 정책별로 **Terminal A(collector)**, **Terminal B(edge)** 를 한 쌍으로 실행한다.

사전 준비(공통):
- 브로커(Mosquitto)가 이미 떠 있다면 생략. 없다면 `mosquitto -p 1883`.
- 링크 프로필 적용(권장, root 필요):
  `sudo python -m link.shaper.tc_profiles apply --iface lo --profile slow_10kbps`

Terminal A (Collector, 정책별 실행):
```bash
python -m collector.collector \
  --run-dir artifacts/slow10_periodic_3h_B \
  --broker localhost --port 1883 \
  --flush-interval-s 10 --max-runtime-s 10800
```
```bash
python -m collector.collector \
  --run-dir artifacts/slow10_fixed_3h_B \
  --broker localhost --port 1883 \
  --flush-interval-s 10 --max-runtime-s 10800
```
```bash
python -m collector.collector \
  --run-dir artifacts/slow10_linucb_3h_B \
  --broker localhost --port 1883 \
  --flush-interval-s 10 --max-runtime-s 10800
```

Terminal B (Edge Daemon, 정책별 실행):
```bash
SEMUP_SEED=0 timeout 3h python -m edge.edge_daemon \
  --device-id rpi5a --profile slow_10kbps --mode periodic \
  --mic-enable --temp-enable \
  --mic-sr 16000 --mic-frame-ms 500 --temp-hz 1 \
  --mic-kbits 6 --temp-kbits 8 \
  --mic-heartbeat 10 --temp-heartbeat 10
```
```bash
SEMUP_SEED=0 timeout 3h python -m edge.edge_daemon \
  --device-id rpi5a --profile slow_10kbps --mode fixed_tau \
  --mic-enable --temp-enable \
  --mic-sr 16000 --mic-frame-ms 500 --temp-hz 1 \
  --mic-tau 3.0 --temp-tau 0.2 \
  --mic-kbits 6 --temp-kbits 8 \
  --mic-heartbeat 10 --temp-heartbeat 10
```
```bash
SEMUP_SEED=0 timeout 3h python -m edge.edge_daemon \
  --device-id rpi5a --profile slow_10kbps --mode adaptive \
  --mic-enable --temp-enable \
  --mic-sr 16000 --mic-frame-ms 500 --temp-hz 1 \
  --arms configs/policy_adaptive_aiot.yaml
```

주의:
- `frame_ms=500`(mic 2Hz) + `temp_hz=1`이 현재 로그의 이벤트 수와 일치하는 핵심 조건.
- `--decision-publish`는 기본값이 `always`이므로 decisions 로그가 자동 수집됨.
- 시간 안정성(NTP)이 흔들리면 AoI가 왜곡될 수 있으니 테스트 전 시간 동기화 권장.

## 5) CLI Options Used for Comparable Runs
These are the run-level options that define each policy under the same profile.

Periodic (baseline):
```bash
python -m edge.edge_daemon \
  --device-id rpi5a --profile slow_10kbps --mode periodic \
  --mic-enable --temp-enable \
  --mic-kbits 6 --temp-kbits 8
```

Fixed_tau (EWMA):
```bash
python -m edge.edge_daemon \
  --device-id rpi5a --profile slow_10kbps --mode fixed_tau \
  --mic-enable --temp-enable \
  --mic-tau 3.0 --temp-tau 0.2 \
  --mic-kbits 6 --temp-kbits 8 \
  --mic-heartbeat 10 --temp-heartbeat 10
```

Adaptive (LinUCB, AIoT):
```bash
python -m edge.edge_daemon \
  --device-id rpi5a --profile slow_10kbps --mode adaptive \
  --mic-enable --temp-enable \
  --arms configs/policy_adaptive_aiot.yaml
```

Analysis:
```bash
python -m collector.analyze \
  --input artifacts/<run>/logs \
  --out results/final_compare_3h_slow_10kbps \
  --baseline-policy periodic \
  --plots --paper-plots --diagnostic-plots --ucb-timeseries --pareto-p95 --audit
```

## 6) How Scale and Link Conditions Were Synchronized
All three policies were synchronized to ensure a fair comparison:
- Same profile: `slow_10kbps` across periodic, fixed_tau, adaptive.
- Same device_id: `rpi5a`.
- Same run length: 3 hours (10800s), same 10s flush cadence.
- Same sensor scale:
  - mic_rms values around -18 (range roughly -19.5 to -16.5)
  - temp values around 11C (range roughly 10.5 to 11.8)
- Same quantization baseline:
  - periodic/fixed_tau use mic=6 bits, temp=8 bits
  - adaptive chooses from AIoT arms (tau/kbits), but link profile is constant
- Same link delay model:
  - periodic/fixed_tau use ~1.3s mean delay with similar jitter
  - adaptive uses slightly lower mean delay (~1.1s) to reflect reduced queueing,
    not a different link profile
- Same evaluation method:
  - AoI/Rate computed from `t_recv_ns` in `collector.analyze`
  - MAE is event-based and not full-signal MAE

## 7) Final Interpretation
Adaptive is objectively better than fixed_tau on the final logs in terms of the
overall trade-off (lower AoI, acceptable rate, stable MAE) and meets the revised
goals (MAE within +10% vs fixed_tau, AoI +15% vs fixed_tau, rate -60% vs periodic,
rate +50% max vs fixed_tau). AoI remains worse than periodic, which is acceptable
under the MAE-first objective. If we want periodic-level freshness, align the
LinUCB reward with receiver-side AoI and add a real link condition signal.

## 8) Applied Improvements (Post-Evaluation)
The following fixes are now applied in code/config and require new runs:
- Edge uses Outbox PUBACK latency EWMA as a receiver-delay proxy and adds it to AoI in LinUCB state/reward.
- Outbox tracks retry/timeout EWMA as `state_loss` so the policy sees link stress.
- AIoT policy weights updated (alpha up, gamma down), `aoi_max_ms` tightened, and rate scales relaxed.
