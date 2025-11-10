#!/usr/bin/env bash
set -euo pipefail

# Run repository health checks

dirname="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${dirname}/.." && pwd)"

cd "${repo_root}"

echo "Running pytest..."
pytest

if command -v ruff >/dev/null 2>&1; then
  echo "Running ruff lint..."
  ruff check
else
  echo "ruff not found; skipping lint." >&2
fi

if command -v mypy >/dev/null 2>&1; then
  if [[ -f mypy.ini ]] || grep -q "^\[tool.mypy" pyproject.toml 2>/dev/null; then
    echo "Running mypy type checks..."
    mypy .
  else
    echo "No mypy configuration detected; skipping type checks." >&2
  fi
else
  echo "mypy not found; skipping type checks." >&2
fi
