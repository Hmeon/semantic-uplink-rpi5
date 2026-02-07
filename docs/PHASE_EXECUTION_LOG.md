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
| Phase 2 (current) | Analyzer architecture decomposition | In progress | KPI/reporting/plot support/runtime/config/orchestrator + load/normalize + decision diagnostics + metrics-core + quality-metrics + plot-generators extracted |

## This Batch (Phase 2.9) - Completed
1. Extracted plotting generator implementations from `collector/analyze.py` into:
   - `collector/plot_generators.py`
   - moved standard/pipeline/paper/diagnostic plotting functions and their internal helpers.
2. Preserved compatibility in `collector/analyze.py`:
   - kept `_try_make_plots`, `_try_make_pipeline_plots`, `_try_make_paper_plots`, `_try_make_diagnostic_plots` as delegation wrappers.
   - reduced `collector/analyze.py` size from `2833` to `878` lines while keeping CLI behavior.
3. Added direct tests for extracted plotting generators:
   - `tests/unit/test_plot_generators_module.py`
   - covered fallback branch when matplotlib is unavailable and filename/slug invariants for generated figure names.
4. Kept KPI semantics unchanged:
   - adaptive-only verdict scope
   - strict PASS per `(profile, sensor)` unchanged.

## Quantitative Verification
- `ruff check .` -> pass
- `pytest -q --cov=common --cov=collector --cov=edge --cov=link --cov-report=term-missing --cov-fail-under=40` -> pass
  - Result: `138 passed, 1 skipped`
  - Total coverage: `56.49%`
  - `collector/plot_generators.py`: `12%` (newly extracted large module baseline)
  - `collector/quality_metrics.py`: `71%`
  - `collector/metrics_core.py`: `86%`
  - `collector/load_normalize.py`: `69%`
  - `collector/decision_diagnostics.py`: `79%`
  - `collector/plot_runtime.py`: `100%`
  - `collector/quality_audit.py`: `64%`
- CLI smoke:
  - `python -m edge.edge_daemon --help` -> pass
  - `python -m collector.collector --help` -> pass
  - `python -m collector.analyze --help` -> pass
  - `python -m experiments.run_scenarios --help` -> pass

## Remaining Objective Risks (Top)
1. `collector/plot_generators.py` is still very large and dense; function-level change blast radius remains high.
2. `collector/plot_generators.py` direct coverage is low (`12%`) despite extraction, leaving many plotting branches unvalidated.
3. Docker-dependent integration path is still skipped on hosts without Docker (`1 skipped`).

## Next Batch (Phase 2.10) Plan
1. Split `collector/plot_generators.py` into domain modules (`plot_standard.py`, `plot_pipeline.py`, `plot_paper.py`, `plot_diagnostic.py`) while preserving wrapper compatibility.
2. Expand direct plotting tests to cover high-risk branches (paper/diagnostic guards, empty-data skips, and representative plot path generation) to raise coverage of extracted plotting modules.
3. Keep strict KPI/report decision logic unchanged (adaptive-only verdict scope and per `(profile, sensor)` strict PASS).
