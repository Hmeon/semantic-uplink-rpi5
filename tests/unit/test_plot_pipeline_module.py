from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from collector.plot_config import PlotConfig
from collector.plot_pipeline import _try_make_pipeline_plots


def _has_metric(created: list[Path], metric: str) -> bool:
    return any(metric in p.name for p in created)


def _pipeline_cfg() -> PlotConfig:
    return PlotConfig(dir_name="figs", formats=("png",), dpi=120)


def _events_for_latency() -> pd.DataFrame:
    base_ts = 1_700_100_000_000_000_000
    rows: list[dict[str, object]] = []
    for i, pol in enumerate(["periodic", "fixed_tau", "adaptive"]):
        ts = base_ts + i * 2_000_000_000
        rows.append(
            {
                "run_id": "run-lat",
                "profile": "slow_10kbps",
                "policy": pol,
                "sensor": "temp",
                "ts": ts,
                "t_recv_ns": ts + 150_000_000,
            }
        )
    return pd.DataFrame(rows)


def test_pipeline_outbox_prefers_t_recv_ns_when_available(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    decisions = pd.DataFrame(
        [
            {
                "run_id": "run-outbox-rx",
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "ts": 1_700_000_000_000_000_000,
                "t_recv_ns": 1_700_000_000_050_000_000,
                "state_q_len": 1,
            },
            {
                "run_id": "run-outbox-rx",
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "ts": 1_700_000_001_000_000_000,
                "t_recv_ns": 1_700_000_001_070_000_000,
                "state_q_len": 3,
            },
        ]
    )

    created = _try_make_pipeline_plots(
        tmp_path,
        events=pd.DataFrame(),
        decisions_enriched=decisions,
        by_run=pd.DataFrame(),
        plot_cfg=_pipeline_cfg(),
    )

    assert created
    assert _has_metric(created, "outbox_pending_ts")


def test_pipeline_outbox_falls_back_to_ts_when_t_recv_ns_missing(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    decisions = pd.DataFrame(
        [
            {
                "run_id": "run-outbox-ts",
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "ts": 1_700_000_010_000_000_000,
                "t_recv_ns": float("nan"),
                "state_q_len": 2,
            },
            {
                "run_id": "run-outbox-ts",
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "ts": 1_700_000_011_000_000_000,
                "t_recv_ns": float("nan"),
                "state_q_len": 4,
            },
        ]
    )

    created = _try_make_pipeline_plots(
        tmp_path,
        events=pd.DataFrame(),
        decisions_enriched=decisions,
        by_run=pd.DataFrame(),
        plot_cfg=_pipeline_cfg(),
    )

    assert created
    assert _has_metric(created, "outbox_pending_ts")


def test_pipeline_latency_boxplot_uses_labels_fallback_for_old_matplotlib(
    monkeypatch,
    tmp_path: Path,
) -> None:
    pytest.importorskip("matplotlib")
    from matplotlib.axes import Axes

    original_boxplot = Axes.boxplot
    calls = {"tick_labels": 0, "labels": 0}

    def _patched_boxplot(self, *args, **kwargs):  # noqa: ANN001
        if "tick_labels" in kwargs:
            calls["tick_labels"] += 1
            raise TypeError("tick_labels unsupported in this test")
        if "labels" in kwargs:
            calls["labels"] += 1
        return original_boxplot(self, *args, **kwargs)

    monkeypatch.setattr(Axes, "boxplot", _patched_boxplot)

    created = _try_make_pipeline_plots(
        tmp_path,
        events=_events_for_latency(),
        decisions_enriched=pd.DataFrame(),
        by_run=pd.DataFrame(),
        plot_cfg=_pipeline_cfg(),
    )

    assert created
    assert _has_metric(created, "rx_delay_box")
    assert calls["tick_labels"] >= 1
    assert calls["labels"] >= 1
