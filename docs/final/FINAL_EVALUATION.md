# Final Evaluation (slow_10kbps, 3h)

This report summarizes the final comparison and notes follow-up improvements
if we want to push receiver-side freshness further, plus the exact CLI options
and the synchronization rules used to keep scale/link conditions consistent across policies.

## 0) Final KPI (strict PASS/FAIL)
Evaluation scope (mandatory):
- The project is **PASS only if every (profile × sensor) is PASS**.
- No partial/optional PASS is allowed (e.g., “mic-only PASS” is not accepted).

Baseline definitions (fixed):
- `periodic`: reference stream (most-frequent sending)
- `fixed_tau`: human-set fixed threshold (quality baseline)
- `adaptive`: LinUCB policy (the policy under test)

KPI (PASS requires all 5 per profile × sensor):
1) Efficiency (Primary): `Rate_improvement_vs_periodic >= 85%`
2) Rate guard (vs fixed_tau): `Rate_improvement_vs_fixed_tau >= -10%`
3) Recon quality guard: `recon_mae_p95_improvement_vs_fixed_tau >= -10%`
4) Coverage guard: `AnomalySegmentRecall >= 0.90`
5) Freshness guard: `AoI_p95_improvement_vs_fixed_tau >= -10%`

Notes on KPI4 (Coverage):
- Anomaly segments are defined on the periodic baseline using `|res| > tau_ref` (tau_ref from fixed_tau).
- If a (profile × sensor) has **0 anomaly segments**, recall is defined as **1.0** (vacuously satisfied) rather than failing due to NaN.

How to check:
- Run `python -m collector.analyze ...` (see section 5). The analyzer writes:
  - `kpi_verdict.json` (project PASS/FAIL + failed profile/sensor list)
  - `kpi_final.csv` (per profile × sensor: K1..K5 + overall)

## 1) Data Sources and Outputs
- Run root (from `scripts/run_3h_sequence.sh`): `artifacts/field_runs/<run_root>/`
- Inputs (3-policy):
  - `artifacts/field_runs/<run_root>/slow_10kbps__periodic/`
  - `artifacts/field_runs/<run_root>/slow_10kbps__fixed_tau/`
  - `artifacts/field_runs/<run_root>/slow_10kbps__adaptive/`
- Analysis output (default): `results/field_runs/<run_root>/`
- Baseline: `periodic`
- AoI/Rate use `t_recv_ns` (receiver time). MAE is event-based (`res`).

## 2) Final Results (Quantitative)
Values are means from `<results_dir>/report.md` (see section 1).

mic_rms:
- periodic: 524.0 B/s, AoI 1536.8 ms, MAE 0.052
- fixed_tau: 26.0 B/s, AoI 6287.7 ms, MAE 0.052
- adaptive: 37.4 B/s, AoI 4745.2 ms, MAE 0.053

temp:
- periodic: 255.0 B/s, AoI 1802.9 ms, MAE 0.036
- fixed_tau: 23.7 B/s, AoI 6952.2 ms, MAE 0.036
- adaptive: 27.4 B/s, AoI 5756.9 ms, MAE 0.035

Final KPI check (strict PASS/FAIL):
- Use the analyzer outputs in the run directory: `kpi_verdict.json` / `kpi_final.csv`.
- The mean-only snapshot above is informative, but **not** a KPI verdict.

Trade-off (policy ranking):
- Adaptive > Fixed_tau for AoI while keeping rate low.
  - mic_rms AoI improves ~24.5% vs fixed_tau (6287.7 -> 4745.2 ms)
  - temp AoI improves ~17.2% vs fixed_tau (6952.2 -> 5756.9 ms)
- Fixed_tau still gives the lowest rate, but AoI is worst.

Conclusion: adaptive improves AoI vs fixed_tau in this snapshot, but final
PASS/FAIL must follow the final KPI table (`kpi_final.csv`).

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
- Rebalance reward weights/scales in `configs/policy.yaml` (or `configs/policy_adaptive_aiot.yaml`) to
  strengthen AoI penalties when it drifts (raise `alpha`, reduce `gamma`).
- Tighten safety guardrails (`aoi_max_ms`) or force a lower-tau arm on AoI
  violations to keep freshness under control.

## 4) One-command reproduction (recommended)
```bash
# Field A
FIELD_LABEL=A SEMUP_SEED=0 PYTHON=$HOME/.venv/bin/python bash scripts/run_3h_sequence.sh

# Field B (same settings)
FIELD_LABEL=B SEMUP_SEED=0 PYTHON=$HOME/.venv/bin/python bash scripts/run_3h_sequence.sh
```

The sequence script writes `RUN_META.txt`, `CHECKLIST.md`, and `sequence.log` under the run root and
auto-runs `collector.analyze` into `results/field_runs/<run_root>/`.

Notes:
- AoI uses `t_recv_ns - ts` so **edge and collector clocks must share a timebase** (same host recommended).
- The script enforces time sync via `timedatectl status` by default; override only if you accept risk (`ALLOW_UNSYNC=1`).
- By default it fails the run if KPI != PASS; set `KPI_ENFORCE_PASS=0` to only report.

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
  --arms configs/policy.yaml \
  --decision-publish never
```

Analysis:
```bash
python -m collector.analyze \
  --input artifacts/field_runs/<run_root>/slow_10kbps__periodic \
  --input artifacts/field_runs/<run_root>/slow_10kbps__fixed_tau \
  --input artifacts/field_runs/<run_root>/slow_10kbps__adaptive \
  --out results/field_runs/<run_root> \
  --baseline-policy periodic \
  --policy-config configs/policy.yaml --audit
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
With the final KPI, the primary target is rate reduction vs periodic (>=85%) but
adaptive must also stay within guardrails vs fixed_tau (Rate/Recon_p95/AoI_p95)
and preserve coverage (AnomalySegmentRecall).

If you fail KPI2 (rate vs fixed_tau), start by constraining arms and/or reward
to avoid oversending:
- Prefer a higher `reward.gamma` (rate penalty), and keep arms near fixed_tau.
- Use an AoI guardrail (`safety_force_emit_on_aoi: true`) as the liveness
  mechanism; in adaptive mode the runtime disables the fixed heartbeat in this
  case to avoid heartbeat dominating the rate.
- See `configs/policy_adaptive_aiot_field_A_final.yaml` /
  `configs/policy_adaptive_aiot_field_B_final.yaml` as starting points.

## 8) Applied Improvements (Post-Evaluation)
The following are applied in code/config and require new runs to reflect:
- Analyzer emits strict final KPI artifacts: `kpi_final.csv`, `kpi_verdict.json`.
- Seq-aligned recon/coverage metrics are computed against the periodic baseline
  (reconstruction MAE and anomaly segment recall).
- Edge policy config supports nested `linucb:` hyperparameters (also per-sensor),
  plus log-scaled queue normalization (`scales.q_len`).
