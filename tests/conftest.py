"""Test configuration for pytest."""

from __future__ import annotations

import sys
from pathlib import Path


# Ensure the project root is on ``sys.path`` so tests can import local packages without
# requiring an editable install. This mirrors the layout on the GitHub runners and fixes
# ModuleNotFoundError issues raised during test collection.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
