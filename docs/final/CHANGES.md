# Changes (ship-it audit)

## 2025-12-23
- Applied link-feedback improvements for LinUCB (PUBACK delay EWMA, loss EWMA)
  and updated AIoT policy weights/safety.
- Final 3h comparison generated: `results/final_compare_3h_slow_10kbps`
  (inputs: `artifacts/slow10_*_3h_B/logs`).
- Outcome: rate target met, MAE target met (<=10% vs fixed_tau), AoI target met
  (>=15% vs fixed_tau), adaptive best trade-off vs fixed_tau.

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
