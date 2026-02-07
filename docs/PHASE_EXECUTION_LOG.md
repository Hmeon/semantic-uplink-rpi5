# Phase Execution Log (Semantic Uplink - RPi5)

Last updated: 2026-02-07

## Objective Baseline (Locked)
- Project intent source:
  - `docs/specs/PROJECT_GOALS.md`
  - `docs/specs/architecture.md`
- Strict KPI scope:
  - adaptive policy only
  - PASS requires all KPI constraints per `(profile, sensor)`

## Phase Progress
| Phase | Goal | Status | Evidence |
|---|---|---|---|
| Phase 0 | Baseline lock | Done | preflight checklist + roadmap docs created |
| Phase 1 | Quality gate hardening | Done | CI smoke + coverage gate 40 + unit test expansion |
| Phase 2 | Analyzer architecture decomposition | Done | analyzer decomposition + artifact-contract/integration/failure-path tests completed |
| Phase 3 | Edge runtime hardening | Done | helper/sensor/lifecycle + UI/publish + controls/peripherals contracts expanded |
| Phase 4 (current) | Adaptive runtime & operational robustness hardening | In progress | adaptive builder + main/signal + CLI matrix branch tests expanded |

## This Batch (Phase 4.1) - Completed
1. Added adaptive runtime builder branch tests (`tests/unit/test_edge_daemon_adaptive_build.py`):
   - `_make_linucb_config` sensor override merge contracts for `arms/reward/safety/diagnostics/scales/linucb`
   - `_make_linucb_config` no-arms rejection and non-positive `tau` scale fallback contracts
   - `_build_policy_runtime` diagnostics merge path (`global dict + sensor dict`) with `events_enabled` override
   - `_build_policy_runtime` diagnostics bool override path (`sensor diagnostics: true`) and non-adaptive skip path
2. Added signal/main execution-path tests (`tests/unit/test_edge_daemon_main_signal.py`):
   - `_install_signals` handler registration and handler behavior (`stop()` + `SystemExit(0)`)
   - `main()` seed resolution matrix (`args.seed`, `SEMUP_SEED`, fallback), adaptive arms loading, run-dir/outbox resolution, and sensor-disable hard fail path
3. Expanded CLI matrix tests (`tests/unit/test_edge_daemon_cli.py`):
   - device YAML UI backend alias mapping (`lcd -> lcd1602`, `ssd1306`, unknown -> `auto`)
   - MQTT TLS default from device YAML and CLI override (`--no-tls`)
   - button default pin/debounce propagation from device YAML
4. Preserved project semantic invariants:
   - KPI strictness and adaptive-only verdict scope unchanged
   - analyzer/report semantics unchanged
   - policy reward/decision semantics unchanged

## Quantitative Verification
- `ruff check .` -> pass
- `pytest -q --cov=common --cov=collector --cov=edge --cov=link --cov-report=term-missing --cov-fail-under=40` -> pass
  - Result: `226 passed, 1 skipped`
  - Total coverage: `80.01%`
  - `edge/edge_daemon.py`: `90%`
  - `edge/ui/status.py`: `100%`
  - `edge/sensors/temp.py`: `71%`
  - `edge/sensors/mic_rms.py`: `49%`
  - `collector/analyze.py`: `94%`
  - `collector/plot_standard.py`: `94%`
  - `collector/plot_pipeline.py`: `93%`
  - `collector/plot_paper.py`: `87%`
  - `collector/plot_diagnostic.py`: `92%`
  - `collector/quality_audit.py`: `76%`
  - `collector/plot_generators.py`: `100%` (compatibility wrappers)
  - `collector/plot_generator_utils.py`: `100%`
  - `collector/quality_metrics.py`: `71%`
  - `collector/metrics_core.py`: `86%`
  - `collector/load_normalize.py`: `79%`
  - `collector/decision_diagnostics.py`: `93%`
  - `collector/plot_runtime.py`: `100%`
- CLI smoke:
  - `python -m edge.edge_daemon --help` -> pass
  - `python -m collector.collector --help` -> pass
  - `python -m collector.analyze --help` -> pass
  - `python -m experiments.run_scenarios --help` -> pass

## Remaining Objective Risks (Top)
1. Docker-dependent integration path is still skipped on hosts without Docker (`1 skipped`).
2. Hardware-bound sensor modules still have low direct test coverage:
   - `edge/sensors/mic_rms.py` backend implementation internals (`sounddevice`/`arecord` process path)
   - `edge/sensors/temp.py` CLI path and some signal/loop termination branches
3. Edge runtime heavy paths partially remain:
   - daemon thread/runtime branches that require real thread start paths (`_maybe_start_ui/_maybe_start_rtc` positive threaded branches under actual scheduler timing)
   - end-to-end adaptive behavior with real `SensorPolicyRuntime` decisions under integrated sensor stream replay

## Next Batch (Phase 4.2) Plan
1. Strengthen integrated adaptive runtime tests using deterministic sample streams and real `SensorPolicyRuntime` to validate decision/event coupling end-to-end.
2. Expand hardware-adjacent module tests (`edge/sensors/mic_rms.py`, `edge/sensors/temp.py`) for CLI/signal/termination branches still under-covered.
3. Keep KPI/report decision logic unchanged (adaptive-only verdict scope and per `(profile, sensor)` strict PASS).
