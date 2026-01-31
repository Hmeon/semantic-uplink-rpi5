# Changelog

All notable changes to this project will be documented in this file.
The format is based on Keep a Changelog, and this project follows Semantic Versioning.

## [0.1.1] - 2026-01-28
### Added
- Configurable MQTT `base_topic` end-to-end (edge publish, collector subscribe, stack, experiments, analyze sizing).

### Fixed
- Event MQTT size estimation now uses the configured `base_topic`.
- Policy decision MQTT size estimation uses the fixed `policy/{device}/decision` topic.
- Outbox ACK-latency EWMA tracking now works with non-default base topics.
- Clarified `tau` semantics (residual threshold in sensor units) in docs.

## [0.1.2] - 2026-01-31
### Added
- KPI4 coverage “segment liveness” option: `safety.coverage_force_emit_on_unhit_segment` (forces at most one emit per anomaly segment without forcing a fixed_tau/safe arm).
- Diagnostics decoupling for payload fairness:
  - `diagnostics.enabled` (decision/learning diagnostics)
  - `diagnostics.events_enabled` (event payload diagnostics)
- Final KPI-oriented policy preset: `configs/policy_poc_covforce_kpi.yaml` (coverage liveness + decision diagnostics on + event diagnostics off).

### Fixed
- Analyzer seed mixing issue in multi-run seq-aligned KPI computation (baseline alignment is now seed-aware).
- Analyzer pipeline diagnostics visibility when `arm_id` is absent (fallback arm id derived from `(tau, kbits)` to compute arm distribution/entropy).

## [0.1.0] - 2025-12-23
### Added
- End-to-end PoC pipeline: edge prediction+policy+outbox→MQTT and collector parquet sink with AoI/MAE/rate analysis.
- Link-feedback inputs for adaptive policy (PUBACK delay EWMA, loss EWMA).
- Automated 3-policy sequence runner and policy configs for AIoT trade-offs.
- Systemd example and operational readiness notes for long-running RPi5 runs.
