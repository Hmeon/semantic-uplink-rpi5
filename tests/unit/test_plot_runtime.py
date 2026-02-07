from __future__ import annotations

import builtins
import json

from collector import plot_runtime as pr
from collector import plotting_support as ps


class _DummyMatplotlib:
    def __init__(self) -> None:
        self.rcParams: dict[str, object] = {}


def test_maybe_import_matplotlib_returns_none_on_import_error(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):  # noqa: ANN001
        if name == "matplotlib" or str(name).startswith("matplotlib"):
            raise ImportError("blocked in test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    matplotlib, plt = pr.maybe_import_matplotlib()
    assert matplotlib is None
    assert plt is None


def test_apply_plot_style_sets_expected_defaults() -> None:
    m = _DummyMatplotlib()
    pr.apply_plot_style(m)
    assert m.rcParams["figure.dpi"] == 120
    assert m.rcParams["axes.labelsize"] == 11
    assert m.rcParams["savefig.bbox"] == "tight"


def test_write_plot_manifest_returns_none_when_empty(tmp_path) -> None:
    ps.clear_plot_manifest()
    out = pr.write_plot_manifest(tmp_path, dir_name="figs", formats=("png",), dpi=300)
    assert out is None
    assert not (tmp_path / "plot_manifest.json").exists()


def test_write_plot_manifest_writes_json_when_present(tmp_path) -> None:
    ps.clear_plot_manifest()
    ps.PLOT_MANIFEST.append(
        {
            "base_name": "temp_slow_compare_rate_bar",
            "formats": ["png"],
            "dpi": 300,
            "size_inches": [6.0, 4.0],
            "files": ["temp_slow_compare_rate_bar.png"],
            "axes": [{"title": "t", "xlabel": "x", "ylabel": "y", "ax_label": ""}],
        }
    )
    path = pr.write_plot_manifest(tmp_path, dir_name="figs", formats=("png", "pdf"), dpi=300)
    assert path is not None
    assert path.exists()
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["plot_cfg"]["dir_name"] == "figs"
    assert doc["plot_cfg"]["formats"] == ["png", "pdf"]
    assert len(doc["figures"]) == 1
