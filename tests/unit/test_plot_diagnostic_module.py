from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from collector.plot_config import PlotConfig
from collector.plot_diagnostic import _try_make_diagnostic_plots


def _diag_events() -> pd.DataFrame:
    base_ts = 1_700_000_100_000_000_000
    rows: list[dict[str, object]] = []
    for i, policy in enumerate(["periodic", "fixed_tau", "adaptive"]):
        ts = base_ts + i * 2_000_000_000
        rows.append(
            {
                "run_id": "run-1",
                "profile": "slow_10kbps",
                "policy": policy,
                "sensor": "temp",
                "ts": ts,
                "t_recv_ns": ts + 120_000_000,
            }
        )
    return pd.DataFrame(rows)


def _diag_summary() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "linucb_safe_forced_rate": 0.30,
                "linucb_forced_reason_aoi_limit_rate": 0.10,
                "linucb_forced_reason_mae_limit_rate": 0.12,
                "linucb_forced_reason_both_rate": 0.08,
                "linucb_switch_rate": 0.20,
                "linucb_rate_limit_skips_per_decision": 0.25,
                "linucb_ucb_exploitation_mean": 0.55,
                "linucb_ucb_exploration_mean": 0.15,
                "linucb_ucb_score_mean": 0.70,
                "linucb_ucb_uncertainty_mean": 0.42,
            }
        ]
    )


def _diag_by_run() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "event_reason_threshold_count": 12,
                "event_reason_heartbeat_count": 5,
                "linucb_rate_limit_skips_total": 3,
                "dup_bytes_ratio": 0.07,
            }
        ]
    )


def _diag_arm_distribution() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "arm_id": 0,
                "frac": 0.65,
                "count": 13,
                "n_decisions": 20,
            },
            {
                "run_id": "run-1",
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "arm_id": 1,
                "frac": 0.35,
                "count": 7,
                "n_decisions": 20,
            },
        ]
    )


def _diag_entropy_windows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "run_id": "run-1",
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "window_idx": 0,
                "window_s": 60,
                "entropy_log2": 0.3,
            },
            {
                "run_id": "run-1",
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "window_idx": 1,
                "window_s": 60,
                "entropy_log2": 0.7,
            },
            {
                "run_id": "run-1",
                "profile": "slow_10kbps",
                "policy": "adaptive",
                "sensor": "temp",
                "window_idx": 2,
                "window_s": 60,
                "entropy_log2": 0.9,
            },
        ]
    )


def _diag_decisions_enriched(*, with_ucb: bool) -> pd.DataFrame:
    base_ts = 1_700_000_200_000_000_000
    rows: list[dict[str, object]] = []
    for i in range(4):
        item: dict[str, object] = {
            "run_id": "run-1",
            "profile": "slow_10kbps",
            "policy": "adaptive",
            "sensor": "temp",
            "arm_id": i % 2,
            "tau": 1.0 if i % 2 == 0 else 2.0,
            "kbits": 8 if i % 2 == 0 else 10,
            "ts": base_ts + i * 1_000_000_000,
            "t_recv_ns": base_ts + i * 1_000_000_000 + 90_000_000,
            "state_q_len": float(i),
        }
        if with_ucb:
            item.update(
                {
                    "ucb_exploitation": 0.5 + 0.1 * i,
                    "ucb_exploration": 0.2 + 0.02 * i,
                    "ucb_score": 0.7 + 0.12 * i,
                    "ucb_alpha": 0.5,
                }
            )
        rows.append(item)
    return pd.DataFrame(rows)


def _has_metric(created: list[Path], metric: str) -> bool:
    return any(metric in p.name for p in created)


def test_diagnostic_plots_high_risk_branches_and_ucb_timeseries(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")

    created = _try_make_diagnostic_plots(
        tmp_path,
        events=_diag_events(),
        decisions_enriched=_diag_decisions_enriched(with_ucb=True),
        by_run=_diag_by_run(),
        summary=_diag_summary(),
        arm_distribution=_diag_arm_distribution(),
        entropy_windows=_diag_entropy_windows(),
        plot_cfg=PlotConfig(dir_name="figs", formats=("png",), dpi=120),
        arm_top_n=5,
        entropy_smooth_window=2,
        ucb_timeseries=True,
    )

    assert created
    assert _has_metric(created, "arm_dist")
    assert _has_metric(created, "entropy_60s")
    assert _has_metric(created, "safe_forced_reasons")
    assert _has_metric(created, "event_reasons")
    assert _has_metric(created, "ucb_decomposition")
    assert _has_metric(created, "ucb_terms_ts")


def test_diagnostic_ucb_timeseries_guard_when_columns_missing(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")

    created = _try_make_diagnostic_plots(
        tmp_path,
        events=_diag_events(),
        decisions_enriched=_diag_decisions_enriched(with_ucb=False),
        by_run=_diag_by_run(),
        summary=_diag_summary(),
        arm_distribution=_diag_arm_distribution(),
        entropy_windows=_diag_entropy_windows(),
        plot_cfg=PlotConfig(dir_name="figs", formats=("png",), dpi=120),
        arm_top_n=5,
        entropy_smooth_window=2,
        ucb_timeseries=True,
    )

    assert created
    assert not _has_metric(created, "ucb_terms_ts")
