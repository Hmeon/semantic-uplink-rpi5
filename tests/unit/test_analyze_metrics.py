from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from collector.analyze import (
    aoi_mean_and_p95,
    aoi_mean_and_p95_from_rx,
    load_events,
    summarize_by_run,
)


def test_aoi_from_ts_zero_delay() -> None:
    ts_ns = np.array([0, 1_000_000_000, 2_000_000_000], dtype=np.int64)
    mean_ms, p95_ms = aoi_mean_and_p95(ts_ns)
    assert mean_ms == pytest.approx(500.0, abs=1e-9)
    assert p95_ms == pytest.approx(950.0, abs=1e-6)


def test_aoi_from_rx_includes_network_delay() -> None:
    gen_ns = np.array([0, 1_000_000_000, 2_000_000_000], dtype=np.int64)
    recv_ns = gen_ns + 100_000_000  # 100ms constant delay
    mean_ms, p95_ms = aoi_mean_and_p95_from_rx(gen_ns, recv_ns)
    assert mean_ms == pytest.approx(600.0, abs=1e-6)
    assert p95_ms == pytest.approx(1050.0, abs=1e-6)


def test_load_events_normalizes_collector_schema(tmp_path: Path) -> None:
    logs_dir = tmp_path / "artifacts" / "runA" / "logs"
    logs_dir.mkdir(parents=True)

    df_in = pd.DataFrame(
        [
            {
                "device_id": "dev1",
                "sensor": "temp",
                "profile": "slow_10kbps",
                "policy": "periodic",
                "seq": 1,
                "ts_ns": 123,
                "t_recv_ns": 456,
                "val": 1.0,
                "pred": 1.0,
                "res": 0.0,
                "tau": 0.0,
                "kbits": 8,
                "mqtt_size_bytes": 111,
            }
        ]
    )
    (logs_dir / "events.csv").write_text(df_in.to_csv(index=False), encoding="utf-8")

    df = load_events([str(tmp_path / "artifacts")])
    assert {"ts", "mqtt_bytes", "run_id"}.issubset(df.columns)
    assert int(df.loc[0, "ts"]) == 123
    assert int(df.loc[0, "mqtt_bytes"]) == 111
    assert str(df.loc[0, "run_id"]) == "runA"


def test_summarize_by_run_uses_recv_time_when_available() -> None:
    gen_ns = np.array([0, 1_000_000_000, 2_000_000_000], dtype=np.int64)
    recv_ns = gen_ns + 100_000_000

    df = pd.DataFrame(
        {
            "run_id": ["run1"] * 3,
            "profile": ["slow_10kbps"] * 3,
            "policy": ["periodic"] * 3,
            "sensor": ["temp"] * 3,
            "device_id": ["dev1"] * 3,
            "seq": [1, 2, 3],
            "ts": gen_ns,
            "t_recv_ns": recv_ns,
            "val": [0.0, 0.0, 0.0],
            "pred": [0.0, 0.0, 0.0],
            "res": [0.0, 0.0, 0.0],
            "tau": [0.0, 0.0, 0.0],
            "kbits": [8, 8, 8],
            "mqtt_bytes": [100, 100, 100],
        }
    )

    by_run = summarize_by_run(df)
    assert len(by_run) == 1
    row = by_run.iloc[0]
    assert row["time_base"] == "recv"
    assert float(row["duration_s"]) == pytest.approx(2.0, abs=1e-9)
    assert float(row["rate_Bps"]) == pytest.approx(150.0, abs=1e-9)
    assert float(row["aoi_mean_ms"]) == pytest.approx(600.0, abs=1e-6)
    assert float(row["aoi_p95_ms"]) == pytest.approx(1050.0, abs=1e-6)

