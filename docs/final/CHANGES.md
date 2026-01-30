# Changes (ship-it audit)

## 2026-01-31
- Finalized “pre-field → field” evaluation docs:
  - `docs/final/FINAL_EVALUATION.md` now covers both synthetic Scenario A/B and single-RPi5 field measurement.
  - Added development-history record for the final synthetic outputs:
    `docs/final/RESULTS_DEV_HISTORY_SCNA_SCNB_POC_COVFORCE_KPI.md`.
- Updated operational entrypoints/examples to use the final KPI preset:
  `configs/policy_poc_covforce_kpi.yaml`.

## 2026-01-30
- KPI4(coverage) 안정화: “fixed_tau 회귀(가드레일)” 대신 **세그먼트 liveness(세그먼트 당 1회 emit 보장)** 옵션 추가.
  - `safety.coverage_force_emit_on_unhit_segment: true`
- Analyzer 정합성 수정: multi-seed 분석에서 baseline/coverage가 seed 간 섞이지 않도록 seed-aware로 계산.
- “파이프라인 진단이 비어 보이는 문제” 해결을 위해 diagnostics를 분리:
  - decision/learning 진단(`diagnostics.enabled`)과 event payload 진단(`diagnostics.events_enabled`)을 분리해
    **payload-fair KPI**와 **풍부한 진단 로그**를 동시에 만족.
- 최종 KPI/진단용 정책 프리셋 추가/확정:
  - `configs/policy_poc_covforce_kpi.yaml` (coverage liveness + decision diagnostics on + event diagnostics off)

## 2026-01-29
- Final KPI set locked (strict PASS/FAIL, per profile × sensor); analyzer now emits
  `kpi_final.csv` and `kpi_verdict.json`.
- Added seq-aligned reconstruction/coverage metrics used by the KPI
  (recon MAE vs periodic + anomaly segment recall).
- Added final AIoT adaptive configs:
  `configs/policy_adaptive_aiot_field_A_final.yaml`,
  `configs/policy_adaptive_aiot_field_B_final.yaml`.
- Added a synthetic run generator for analysis/KPI smoke tests:
  `scripts/generate_synthetic_run.py`.

## 2025-12-23
- Applied link-feedback improvements for LinUCB (PUBACK delay EWMA, loss EWMA)
  and updated AIoT policy weights/safety.
- Final 3h comparison generated: `results/final_compare_3h_slow_10kbps`
  (inputs: `artifacts/slow10_*_3h_B/logs`).
- Outcome: see final KPI definition in `docs/specs/architecture.md` (older success
  criteria notes are deprecated).

## 2025-12-22
- Added 3-policy automated sequence runner (`scripts/run_3h_sequence.sh`) with NTP freeze support.
- Added AIoT-focused policy config (`configs/policy_adaptive_aiot.yaml`) and quality-focused config.
- Refreshed README/CODEX/AGENTS to align with current run/analysis commands and Parquet guidance.

## 2025-12-20
- Fixed editable install failure by defining explicit package discovery in `pyproject.toml` (no runtime behavior change).
- Updated Matplotlib boxplot calls to use `tick_labels` to avoid deprecation warnings (plot output unchanged).
- Synced RPi5 install guidance in README/CODEX/AGENTS to use `pip install -e .[analysis,hw]` (dev: `.[dev,analysis,hw]`).
- Expanded `hw` extra to include `gpiozero` and `rpi-lgpio` so hardware installs match requirements.txt.
- Default behavior unchanged; diagnostics and optional safety settings remain default-off.
