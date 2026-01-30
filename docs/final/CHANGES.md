# Changes (ship-it audit)

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
