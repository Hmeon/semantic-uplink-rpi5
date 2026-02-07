from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from collector.metrics_core import (
    aoi_mean_and_p95_from_rx,
    dedup_and_sort,
    estimate_payload_bytes,
    summarize_by_run,
)


def test_dedup_and_sort_requires_key_columns() -> None:
    with pytest.raises(ValueError):
        dedup_and_sort(pd.DataFrame({"run_id": ["r1"], "device_id": ["d1"]}))


def test_estimate_payload_bytes_uses_precomputed_column() -> None:
    df = pd.DataFrame({"mqtt_bytes": [111, 222, 333]})
    out = estimate_payload_bytes(df)
    assert out.tolist() == [111, 222, 333]
    assert str(out.dtype) == "int64"


def test_estimate_payload_bytes_reconstructs_event_with_topic() -> None:
    df = pd.DataFrame(
        [
            {
                "ts": 1,
                "seq": 1,
                "device_id": "dev1",
                "sensor": "temp",
                "val": 1.0,
                "pred": 1.0,
                "res": 0.0,
                "tau": 0.2,
                "kbits": 8,
                "profile": "slow_10kbps",
                "policy": "periodic",
                "topic": "edge/dev1/temp/event",
            }
        ]
    )
    out = estimate_payload_bytes(df)
    assert len(out) == 1
    assert int(out.iloc[0]) > 0


def test_aoi_mean_and_p95_from_rx_validates_shape() -> None:
    with pytest.raises(ValueError):
        aoi_mean_and_p95_from_rx(np.array([1, 2]), np.array([1, 2, 3]))


def test_summarize_by_run_missing_columns_raises() -> None:
    with pytest.raises(ValueError):
        summarize_by_run(pd.DataFrame({"run_id": ["r1"]}))


def test_summarize_by_run_ts_base_and_send_ratio_estimation() -> None:
    df = pd.DataFrame(
        {
            "run_id": ["run1", "run1"],
            "profile": ["slow_10kbps", "slow_10kbps"],
            "policy": ["periodic", "periodic"],
            "sensor": ["temp", "temp"],
            "device_id": ["dev1", "dev1"],
            "seq": [1, 3],
            "ts": [0, 2_000_000_000],
            "val": [0.0, 0.0],
            "pred": [0.0, 0.0],
            "res": [0.0, 0.0],
            "tau": [0.2, 0.2],
            "kbits": [8, 8],
            "mqtt_bytes": [100, 100],
            "event_reason": ["THRESHOLD", "HEARTBEAT"],
        }
    )

    out = summarize_by_run(df)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["time_base"] == "ts"
    assert int(row["n_suppressed_est"]) == 1
    assert int(row["n_samples_est"]) == 3
    assert float(row["send_ratio"]) == pytest.approx(2.0 / 3.0, abs=1e-12)
    assert float(row["event_reason_threshold_frac"]) == pytest.approx(0.5, abs=1e-12)
    assert float(row["event_reason_heartbeat_frac"]) == pytest.approx(0.5, abs=1e-12)
    assert math.isnan(float(row["rx_delay_mean_ms"]))
