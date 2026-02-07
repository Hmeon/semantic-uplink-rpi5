"""Plot configuration primitives shared by analyzer components."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlotConfig:
    """Plot output configuration (paper-ready)."""

    dir_name: str = "figs"
    formats: tuple[str, ...] = ("png", "pdf")
    dpi: int = 300


def parse_plot_formats(raw: str) -> tuple[str, ...]:
    """Parse comma-separated plot formats with validation."""
    items = [x.strip().lower() for x in str(raw).split(",") if x.strip()]
    if not items:
        return ("png",)
    allowed = {"png", "pdf", "svg"}
    bad = [x for x in items if x not in allowed]
    if bad:
        raise ValueError(f"invalid --plot-formats: {bad} (allowed: {sorted(allowed)})")
    out: list[str] = []
    for x in items:
        if x not in out:
            out.append(x)
    return tuple(out)


__all__ = ["PlotConfig", "parse_plot_formats"]
