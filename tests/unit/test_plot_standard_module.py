from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from collector.plot_config import PlotConfig
from collector.plot_standard import _try_make_plots


def _has_metric(created: list[Path], metric: str) -> bool:
    return any(metric in p.name for p in created)


def _summary_with_reward_components() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "periodic",
                "sensor": "temp",
                "rate_Bps": 110.0,
                "aoi_mean_ms": 1300.0,
                "aoi_p95_ms": 2200.0,
                "mae_event_mean": 1.2,
                "mae_event_p95": 2.0,
                "kbits_mean": 8.0,
                "linucb_reward_aoi_mean": -0.3,
                "linucb_reward_mae_mean": -0.2,
                "linucb_reward_rate_mean": -0.5,
            },
            {
                "profile": "slow_10kbps",
                "policy": "fixed_tau",
                "sensor": "temp",
                "rate_Bps": 90.0,
                "aoi_mean_ms": 1000.0,
                "aoi_p95_ms": 1600.0,
                "mae_event_mean": 0.9,
                "mae_event_p95": 1.4,
                "kbits_mean": 8.0,
                "linucb_reward_aoi_mean": -0.2,
                "linucb_reward_mae_mean": -0.1,
                "linucb_reward_rate_mean": -0.4,
            },
            {
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "rate_Bps": 70.0,
                "aoi_mean_ms": 800.0,
                "aoi_p95_ms": 1200.0,
                "mae_event_mean": 0.7,
                "mae_event_p95": 1.0,
                "kbits_mean": 8.0,
                "linucb_reward_aoi_mean": -0.1,
                "linucb_reward_mae_mean": -0.05,
                "linucb_reward_rate_mean": -0.3,
            },
        ]
    )


def test_standard_plots_generate_optional_p95_and_reward_components(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    created = _try_make_plots(
        tmp_path,
        _summary_with_reward_components(),
        plot_cfg=PlotConfig(dir_name="figs", formats=("png",), dpi=120),
        pareto_p95=True,
    )

    assert created
    assert _has_metric(created, "pareto_rate_vs_aoi_p95")
    assert _has_metric(created, "reward_components_bar")


def test_standard_plots_skip_reward_components_when_columns_missing(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    summary = _summary_with_reward_components().drop(
        columns=["linucb_reward_aoi_mean", "linucb_reward_mae_mean", "linucb_reward_rate_mean"]
    )
    created = _try_make_plots(
        tmp_path,
        summary,
        plot_cfg=PlotConfig(dir_name="figs", formats=("png",), dpi=120),
        pareto_p95=False,
    )

    assert created
    assert _has_metric(created, "pareto_rate_vs_aoi_mean")
    assert not _has_metric(created, "pareto_rate_vs_aoi_p95")
    assert not _has_metric(created, "reward_components_bar")
