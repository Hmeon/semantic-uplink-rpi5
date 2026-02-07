"""Shared plotting helpers for analyzer/reporting modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# plot manifest (for audits; populated by save_figure_multi)
PLOT_MANIFEST: list[dict[str, object]] = []


def clear_plot_manifest() -> None:
    """Clear in-memory plot manifest entries."""
    PLOT_MANIFEST.clear()


def slugify_part(x: str) -> str:
    """Normalize a plot-name token into a filesystem-safe slug."""
    return (
        str(x)
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
        .replace(":", "_")
        .replace("|", "_")
    )


def build_fig_basename(
    *,
    sensor: str,
    profile: str,
    policy: str,
    metric: str,
    run_id: str | None = None,
) -> str:
    """Build canonical figure basename used across analyze/reporting/audit."""
    base = (
        f"{slugify_part(sensor)}_{slugify_part(profile)}_"
        f"{slugify_part(policy)}_{slugify_part(metric)}"
    )
    if run_id:
        base = f"{base}__{slugify_part(run_id)}"
    return base


def save_figure_multi(
    fig: Any,
    out_dir: Path,
    *,
    base_name: str,
    formats: tuple[str, ...],
    dpi: int,
) -> list[Path]:
    """Save one figure to multiple formats and record manifest metadata."""
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        fig.tight_layout()
    except Exception:
        pass

    created: list[Path] = []
    for fmt in formats:
        out_path = out_dir / f"{base_name}.{fmt}"
        save_kwargs = {
            "bbox_inches": "tight",
            "pad_inches": 0.02,
            "facecolor": "white",
        }
        if fmt == "png":
            fig.savefig(out_path, dpi=int(dpi), **save_kwargs)
        else:
            fig.savefig(out_path, **save_kwargs)
        created.append(out_path)

    try:
        size_in = fig.get_size_inches()
        axes = []
        for ax in fig.get_axes():
            axes.append(
                {
                    "title": str(ax.get_title() or ""),
                    "xlabel": str(ax.get_xlabel() or ""),
                    "ylabel": str(ax.get_ylabel() or ""),
                    "ax_label": str(ax.get_label() or ""),
                }
            )
        PLOT_MANIFEST.append(
            {
                "base_name": base_name,
                "formats": list(formats),
                "dpi": int(dpi),
                "size_inches": [float(size_in[0]), float(size_in[1])],
                "files": [p.name for p in created],
                "axes": axes,
            }
        )
    except Exception:
        pass

    return created


__all__ = [
    "PLOT_MANIFEST",
    "build_fig_basename",
    "clear_plot_manifest",
    "save_figure_multi",
    "slugify_part",
]
