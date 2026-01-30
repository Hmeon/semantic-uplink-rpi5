# Final Evaluation (slow_10kbps, 3h) — Pre-field (Synthetic A/B) + Field (Single RPi5)

Updated: 2026-01-31

This project has two “final” evaluation stages:

1) **Pre-field (Synthetic Scenarios A/B)** — field-like, bias-minimized synthetic runs used to validate KPI logic and the adaptive mode in a controlled way.
2) **Field measurement (single RPi5)** — a real hardware run on one device, executed as a 3-policy sequence.

The intent is: **A/B synthetic PASS** → then proceed to **single-RPi5 field measurement** with the same evaluation rules.

For the development history (what was tried, what failed, what was fixed), see:
- `docs/final/RESULTS_DEV_HISTORY_SCNA_SCNB_POC_COVFORCE_KPI.md`
- `docs/specs/KPI_DIAGNOSIS_AND_RECOMMENDATION.md`

---

## 0) Final KPI (strict PASS/FAIL)

Evaluation scope (mandatory):
- The project is **PASS only if every (profile × sensor) is PASS**.
- No partial/optional PASS is allowed (e.g., “mic-only PASS” is not accepted).

Baselines (fixed):
- `periodic`: reference stream (most-frequent sending)
- `fixed_tau`: fixed residual threshold baseline (quality reference)
- `adaptive`: LinUCB policy (the policy under test)

KPI (PASS requires all 5 per profile × sensor):
1) Efficiency (Primary): `Rate_improvement_vs_periodic >= 85%`
2) Rate guard (vs fixed_tau): `Rate_improvement_vs_fixed_tau >= -10%`
3) Recon quality guard: `recon_mae_p95_improvement_vs_fixed_tau >= -10%`
4) Coverage guard: `AnomalySegmentRecall >= 0.90`
5) Freshness guard: `AoI_p95_improvement_vs_fixed_tau >= -10%`

Notes on KPI4 (Coverage):
- Anomaly segments are defined on the periodic baseline using `|res| > tau_ref` (`tau_ref` derived from fixed_tau).
- Segments are **consecutive** runs with length **>= 2 samples**.
- A segment is considered “hit” if the candidate emits **at least 1 event** within that segment.

The analyzer emits:
- `kpi_verdict.json` (project PASS/FAIL + failures)
- `kpi_final.csv` (K1..K5 + overall per profile × sensor)

---

## 1) Final evaluation policy config (recommended)

Use:
- `configs/policy_poc_covforce_kpi.yaml`

Why this config is the final preset:
- **KPI4 stability without “fixed_tau 회귀(guardrail)”**: `coverage_force_emit_on_unhit_segment: true` enforces “one emit per anomaly segment” *without forcing a safe arm*.
- **Payload-fair KPI** while still getting rich diagnostics:
  - `diagnostics.enabled: true` → decision/learning diagnostics are available (UCB, forced_reason, reward components, etc.).
  - `diagnostics.events_enabled: false` → event payload is not inflated by diagnostic fields (avoids biasing Rate vs fixed_tau).

---

## 2) Pre-field synthetic evaluation (Scenario A/B)

### 2.1 What the final outputs are (this workspace)
The final pre-field synthetic outputs are the following 8 result folders:
- `results/scnA_poc_covforce_kpi_rep00`
- `results/scnA_poc_covforce_kpi_rep01`
- `results/scnA_poc_covforce_kpi_rep02`
- `results/scnA_poc_covforce_kpi_agg_seeded`
- `results/scnB_poc_covforce_kpi_rep00`
- `results/scnB_poc_covforce_kpi_rep01`
- `results/scnB_poc_covforce_kpi_rep02`
- `results/scnB_poc_covforce_kpi_agg_seeded`

Each folder contains:
- `report.md`, `kpi_verdict.json`, `kpi_final.csv`
- `metrics_by_run.csv`, `metrics_summary.csv|parquet`, `quality_audit.*`
- `linucb_arm_distribution.csv`, `linucb_entropy_60s.csv`
- `figs/` + `plot_manifest.json`

### 2.2 Minimal reproduction template
Generate adaptive artifacts (decisions logged locally; not counted as event uplink):
```bash
python scripts/generate_synthetic_run.py --model field --scenario B --policy adaptive --seed 2 \
  --run-dir artifacts/field_scnB_adaptive_3h_poc_covforce_kpi_rep02 --overwrite \
  --arms-config configs/policy_poc_covforce_kpi.yaml --decision-publish local
```

Analyze with full outputs:
```bash
python -m collector.analyze \
  -i artifacts/field_scnB_periodic_3h_v11_rep02 \
  -i artifacts/field_scnB_fixed_tau_3h_v11_rep02 \
  -i artifacts/field_scnB_adaptive_3h_poc_covforce_kpi_rep02 \
  -o results/scnB_poc_covforce_kpi_rep02 \
  --baseline-policy periodic \
  --policy-config configs/policy_poc_covforce_kpi.yaml \
  --audit --plots --paper-plots --diagnostic-plots --ucb-timeseries --pareto-p95 --save-parquet
```

For the complete seed-0..2 aggregated reproduction, see:
- `docs/final/RESULTS_DEV_HISTORY_SCNA_SCNB_POC_COVFORCE_KPI.md`

---

## 3) Field measurement (single RPi5): 3-policy × 3-hour sequence

Recommended runner:
- `scripts/run_3h_sequence.sh`

It executes on a **single RPi5**:
- periodic → fixed_tau → adaptive (each for `RUN_SECONDS`, default 10800s)
- runs a collector per phase
- runs `collector.analyze` at the end

### 3.1 Preflight checklist (practical)
- Packages (RPi OS/Debian example):
  ```bash
  sudo apt-get update
  sudo apt-get install -y mosquitto mosquitto-clients alsa-utils i2c-tools coreutils python3-venv
  python3 -m venv .venv && source .venv/bin/activate
  pip install -e .[analysis,hw]
  ```
- Broker running:
  - `mosquitto -c infra/mosquitto/mosquitto.conf` (or run via systemd)
- Sensor presence:
  - DS18B20: `ls /sys/bus/w1/devices/28-*/w1_slave` (override via `W1_PATH`)
  - Mic: `arecord -l` and set `MIC_DEVICE` (example `hw:2,0`)
- Time sync:
  - `timedatectl status` → `System clock synchronized: yes` recommended

### 3.2 Smoke test first (2min × 3 policies)
Before a full 9-hour run:
```bash
RUN_SECONDS=120 KPI_ENFORCE_PASS=0 FIELD_LABEL=SMOKE \
  ADAPTIVE_ARMS=configs/policy_poc_covforce_kpi.yaml \
  DECISION_PUBLISH=event \
  ANALYZE_EXTRA_ARGS="--diagnostic-plots --ucb-timeseries --pareto-p95 --save-parquet" \
  PYTHON=$HOME/.venv/bin/python bash scripts/run_3h_sequence.sh
```

### 3.3 Full field run (3h × 3 policies)
```bash
FIELD_LABEL=A SEMUP_SEED=0 DEVICE_ID=rpi5a \
  ADAPTIVE_ARMS=configs/policy_poc_covforce_kpi.yaml \
  DECISION_PUBLISH=event \
  ANALYZE_EXTRA_ARGS="--diagnostic-plots --ucb-timeseries --pareto-p95 --save-parquet" \
  PYTHON=$HOME/.venv/bin/python bash scripts/run_3h_sequence.sh
```

Outputs:
- Run root: `artifacts/field_runs/<run_root>/`
- Results: `results/field_runs/<run_root>/`

What to check:
- `results/field_runs/<run_root>/kpi_verdict.json`
- `results/field_runs/<run_root>/kpi_final.csv`
- `results/field_runs/<run_root>/report.md`

Archive (recommended):
```bash
tar -czf "field_run_<run_root>.tar.gz" "artifacts/field_runs/<run_root>" "results/field_runs/<run_root>"
```

---

## 4) Fairness & diagnostics (why some knobs matter)

To keep KPI comparisons meaningful:
- **Do not inflate event payload only for adaptive.**
  - In this project, event-level diagnostics (e.g., `event_reason`) can change MQTT bytes, which biases KPI2.
  - The final preset (`configs/policy_poc_covforce_kpi.yaml`) keeps event payload diagnostics off via `diagnostics.events_enabled: false`.
- **Decision diagnostics are safe to enable for analysis** as long as you keep decisions out of the event uplink:
  - Synthetic: `--decision-publish local`
  - Field: `DECISION_PUBLISH=event` is fine if you treat decision logs as “diagnostic telemetry”; if you want strict uplink-only accounting, keep decisions off and accept reduced pipeline diagnostics.

---

## 5) Applied improvements (final state)

These are part of the final repo state and reflected in the final outputs:
- Strict KPI artifacts: `kpi_final.csv`, `kpi_verdict.json` (+ `report.md`).
- Seed-aware, seq-aligned KPI calculation (prevents seed mixing in multi-run analysis).
- KPI4 stabilization without fixed_tau “guardrail regression”:
  - `safety.coverage_force_emit_on_unhit_segment: true`
- Diagnostics decoupling:
  - `diagnostics.enabled` (decision/learning) vs `diagnostics.events_enabled` (event payload)
- Analyzer “pipeline diagnostics” made visible when decision logs are present:
  - arm distribution + action entropy outputs

