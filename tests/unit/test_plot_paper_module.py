from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from collector.plot_config import PlotConfig
from collector.plot_paper import _try_make_paper_plots


def _build_events(*, with_recv: bool) -> pd.DataFrame:
    base_ts = 1_700_000_000_000_000_000
    rows: list[dict[str, object]] = []
    for i in range(8):
        ts = base_ts + i * 1_000_000_000
        recv = ts + 200_000_000 + (i % 2) * 30_000_000
        rows.append(
            {
                "run_id": "run-1",
                "device_id": "dev-1",
                "profile": "cellular_var",
                "policy": "adaptive",
                "sensor": "temp",
                "seq": i,
                "ts": ts,
                "t_recv_ns": recv if with_recv else np.nan,
                "tau": 1.0,
                "kbits": 8 if i % 2 == 0 else 10,
                "res": 0.2 + 0.1 * i,
            }
        )
    return pd.DataFrame(rows)


def _build_decisions(events: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for i, r in events.reset_index(drop=True).iterrows():
        rows.append(
            {
                "run_id": str(r["run_id"]),
                "device_id": str(r["device_id"]),
                "ts": int(r["ts"]),
                "tau": float(r["tau"]),
                "kbits": int(r["kbits"]),
                "state_res": float(r["res"]),
                "reward": 1.0 - 0.05 * i,
                "state_aoi": 6200.0 if i % 2 == 1 else 800.0,
                "state_res_var": 0.01 * (i + 1),
                "state_loss": 0.02 * (i % 3),
                "state_q_len": float(i % 4),
            }
        )
    return pd.DataFrame(rows)


def _has_metric(created: list[Path], metric: str) -> bool:
    return any(metric in p.name for p in created)


def test_paper_plots_representative_run_with_t_recv_generates_timeline(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    events = _build_events(with_recv=True)
    decisions = _build_decisions(events)

    created = _try_make_paper_plots(
        tmp_path,
        events=events,
        decisions=decisions,
        summary=pd.DataFrame(),
        plot_cfg=PlotConfig(dir_name="figs", formats=("png",), dpi=120),
        policy_config_path="does/not/exist.yaml",
        reward_window=4,
        action_bins=4,
        top_actions=6,
        cellular_var_period_s=1,
    )

    assert created
    assert _has_metric(created, "action_heatmap")
    assert _has_metric(created, "reward_by_profile_ts")
    assert _has_metric(created, "feature_weights")
    assert _has_metric(created, "reward_ts")
    assert _has_metric(created, "cumulative_regret")
    assert _has_metric(created, "stability_abs_res_ts")
    assert _has_metric(created, "timeline")


def test_paper_plots_without_t_recv_skips_timeline_but_keeps_core_rep_plots(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    events = _build_events(with_recv=False)
    decisions = _build_decisions(events)

    created = _try_make_paper_plots(
        tmp_path,
        events=events,
        decisions=decisions,
        summary=pd.DataFrame(),
        plot_cfg=PlotConfig(dir_name="figs", formats=("png",), dpi=120),
        policy_config_path="does/not/exist.yaml",
        reward_window=4,
        action_bins=4,
        top_actions=6,
        cellular_var_period_s=1,
    )

    assert created
    assert _has_metric(created, "reward_ts")
    assert _has_metric(created, "cumulative_regret")
    assert _has_metric(created, "stability_abs_res_ts")
    assert not _has_metric(created, "timeline")
