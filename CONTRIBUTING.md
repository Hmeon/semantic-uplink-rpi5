# Contributing

Thanks for your interest in contributing.

## Development setup
- Python: 3.10+
- Install (editable):
  - `python -m venv .venv`
  - Linux/macOS: `. .venv/bin/activate`
  - Windows PowerShell: `.\\.venv\\Scripts\\Activate.ps1`
  - `pip install -e .[dev,analysis]` (hardware: add `hw` → `pip install -e .[dev,analysis,hw]`)

## Quality checks
- Tests: `python -m pytest -q`
- Lint: `ruff check`

## Pull requests
- Keep changes focused and well-scoped.
- Update docs/config examples when behavior changes.
- Add unit tests for bug fixes and new logic when feasible.

## Reporting issues
- Use GitHub Issues with:
  - expected vs actual behavior
  - repro steps (commands/configs)
  - logs (`artifacts/.../stack_logs/*.log` when using `stack.pi_stack`)
