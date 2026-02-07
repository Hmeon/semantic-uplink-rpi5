"""Matplotlib runtime/style and plot-manifest write helpers."""

from __future__ import annotations

import json
from pathlib import Path

from collector.plotting_support import PLOT_MANIFEST


def maybe_import_matplotlib():
    """Import matplotlib in headless mode; return (matplotlib, pyplot) or (None, None)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt  # type: ignore

        return matplotlib, plt
    except Exception:
        return None, None


def apply_plot_style(matplotlib) -> None:
    """Apply deterministic, paper-friendly style defaults."""
    matplotlib.rcParams.update(
        {
            "figure.dpi": 120,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.02,
            "axes.titlesize": 12,
            "axes.labelsize": 11,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "legend.fontsize": 10,
            "lines.linewidth": 1.8,
            "axes.grid": False,
            "grid.alpha": 0.25,
        }
    )


def write_plot_manifest(
    out_dir: Path,
    *,
    dir_name: str,
    formats: tuple[str, ...],
    dpi: int,
) -> Path | None:
    """Write plot manifest JSON if manifest entries exist."""
    if not PLOT_MANIFEST:
        return None

    manifest_path = out_dir / "plot_manifest.json"
    manifest = {
        "plot_cfg": {
            "dir_name": str(dir_name),
            "formats": list(formats),
            "dpi": int(dpi),
        },
        "figures": PLOT_MANIFEST,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return manifest_path


__all__ = [
    "apply_plot_style",
    "maybe_import_matplotlib",
    "write_plot_manifest",
]
