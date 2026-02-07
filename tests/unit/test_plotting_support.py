from __future__ import annotations

from pathlib import Path

from collector import plotting_support as ps


class _DummyAxis:
    def __init__(self, title: str, xlabel: str, ylabel: str, label: str = "") -> None:
        self._title = title
        self._xlabel = xlabel
        self._ylabel = ylabel
        self._label = label

    def get_title(self) -> str:
        return self._title

    def get_xlabel(self) -> str:
        return self._xlabel

    def get_ylabel(self) -> str:
        return self._ylabel

    def get_label(self) -> str:
        return self._label


class _DummyFig:
    def __init__(self) -> None:
        self.savefig_calls: list[tuple[str, int | None]] = []

    def tight_layout(self) -> None:
        return None

    def savefig(self, out_path: Path, *, dpi: int | None = None, **_kwargs) -> None:
        Path(out_path).write_bytes(b"figure")
        self.savefig_calls.append((Path(out_path).name, dpi))

    def get_size_inches(self):
        return (6.4, 4.8)

    def get_axes(self):
        return [_DummyAxis("t", "x", "y")]


def test_slugify_and_basename_contract() -> None:
    assert ps.slugify_part("temp/sensor:1|A B") == "temp_sensor_1_A_B"
    name = ps.build_fig_basename(
        sensor="temp/sensor",
        profile="slow 10kbps",
        policy="adaptive",
        metric="rate:bar",
        run_id="run|01",
    )
    assert name == "temp_sensor_slow_10kbps_adaptive_rate_bar__run_01"


def test_save_figure_multi_writes_files_and_manifest(tmp_path) -> None:
    ps.clear_plot_manifest()
    fig = _DummyFig()
    created = ps.save_figure_multi(
        fig,
        tmp_path,
        base_name="temp_slow_adaptive_rate_bar",
        formats=("png", "pdf"),
        dpi=300,
    )

    assert [p.name for p in created] == [
        "temp_slow_adaptive_rate_bar.png",
        "temp_slow_adaptive_rate_bar.pdf",
    ]
    assert all(p.exists() for p in created)

    assert len(ps.PLOT_MANIFEST) == 1
    m = ps.PLOT_MANIFEST[0]
    assert m["base_name"] == "temp_slow_adaptive_rate_bar"
    assert m["formats"] == ["png", "pdf"]
    assert m["dpi"] == 300
    assert m["files"] == [p.name for p in created]
