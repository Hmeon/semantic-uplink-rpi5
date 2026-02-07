from __future__ import annotations

import pytest

from collector.plot_config import PlotConfig, parse_plot_formats


def test_plot_config_defaults() -> None:
    cfg = PlotConfig()
    assert cfg.dir_name == "figs"
    assert cfg.formats == ("png", "pdf")
    assert cfg.dpi == 300


def test_parse_plot_formats_valid_and_dedup() -> None:
    assert parse_plot_formats("png,pdf,png,svg") == ("png", "pdf", "svg")
    assert parse_plot_formats("  ") == ("png",)


def test_parse_plot_formats_invalid_raises() -> None:
    with pytest.raises(ValueError, match="invalid --plot-formats"):
        parse_plot_formats("png,jpg")
