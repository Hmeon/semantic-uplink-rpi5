# Repository Guidelines

## Project Structure & Module Organization
- `edge/`: sensing → prediction → policy (LinUCB) → quantization → uploader/UI entry (`edge.edge_daemon`).
- `collector/`: MQTT subscriber, metrics, persistence, analysis tools.
- `link/`: `tc`/`netem` profiles and helpers to shape constrained links.
- `common/`: shared schemas, quantization, time/MQTT helpers.
- `configs/`: YAML for device, policy, and link profiles; tweak here to change runs without code edits.
- `scripts/`: thin wrappers for typical flows (`apply_profile.sh`, `start_collector.sh`, `start_edge.sh`).
- `tests/`: `unit/` and `integration/`; `data/` and `logs/` hold run artifacts.

## Build, Test, and Development Commands
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]                # editable install with test/lint tools
ruff .                               # lint (line length 100; see pyproject.toml)
pytest -q                            # unit + integration tests (pytest-asyncio enabled)
python -m link.shaper.tc_profiles apply lo slow_10kbps   # apply link profile
python -m collector.collector        # start collector
python -m edge.edge_daemon --mode adaptive --arms configs/policy.yaml
```
`scripts/` exposes the same flows if you prefer shell entrypoints; keep the virtualenv active.

## Coding Style & Naming Conventions
- Python 3.10+, 4-space indents, target line length 100. Run `ruff` before pushing; primary scope is `edge/policy` and `tests/`.
- Naming: modules and functions `snake_case`, classes `CamelCase`, constants `UPPER_SNAKE`. Preserve existing topic/config naming in YAML.
- Prefer explicit type hints and keep loguru output concise to avoid noisy edge logs.

## Testing Guidelines
- Place new fast checks in `tests/unit/`, scenario/flow checks in `tests/integration/`.
- Name files and functions `test_*.py`; mark async tests with `@pytest.mark.asyncio` (see `tests/unit/test_outbox.py`).
- Add fixtures in `tests/conftest.py` when multiple suites share setup (sys.path is already injected there).
- Cover new policy arms, quantization paths, and uploader/resilience edges; prefer deterministic inputs.

## Commit & Pull Request Guidelines
- Branches: `feat/*`, `fix/*`, `chore/*` (matches existing practice).
- Commits: `type(scope): subject` (e.g., `feat(edge): add ar1 predictor`); keep subjects imperative and <72 chars.
- PRs: include what changed, why, how to test (commands run), and screenshots/metrics if UI or analysis output changed. Link issues when available.
- Ensure lint + pytest pass before requesting review; mention skipped tests or constraints explicitly.

## Security & Configuration Tips
- Never commit real broker credentials or private link profiles; sample YAML in `configs/` should stay generic.
- Outbox/collector paths (`data/`, `logs/`) hold run artifacts—clean or gitignore before publishing.
- When shaping links, prefer loopback (`lo`) during dev to avoid disrupting host connectivity.
