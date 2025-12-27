"""SQLite storage backend (optional).

The default collector sink is Parquet (`collector/collector.py`). This module is kept as a
lightweight alternative for future work, and provides only schema creation for now.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def ensure_schema(path: str | Path = "collector.sqlite") -> None:
    """Ensure the SQLite schema exists for collector storage.

    Args:
        path: Path to the SQLite database file.

    Returns:
        None.

    Raises:
        sqlite3.Error: If schema creation fails.

    Side Effects:
        - Creates directories and initializes tables/indexes.

    Contract:
        - Idempotent; safe to call multiple times.

    Failure Modes:
        - Database errors propagate to the caller.
    """
    p = Path(path)
    if p.parent:
        p.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(str(p)) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
              device_id TEXT NOT NULL,
              sensor TEXT NOT NULL,
              seq INTEGER NOT NULL,
              ts_ns INTEGER NOT NULL,
              t_recv_ns INTEGER NOT NULL,
              val REAL NOT NULL,
              pred REAL NOT NULL,
              res REAL NOT NULL,
              tau REAL NOT NULL,
              kbits INTEGER NOT NULL,
              profile TEXT NOT NULL,
              policy TEXT NOT NULL,
              topic TEXT NOT NULL,
              mqtt_size_bytes INTEGER NOT NULL,
              PRIMARY KEY (device_id, sensor, seq)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
              ts INTEGER NOT NULL,
              t_recv_ns INTEGER NOT NULL,
              device_id TEXT NOT NULL,
              state_aoi REAL NOT NULL,
              state_res REAL NOT NULL,
              state_res_var REAL NOT NULL,
              state_loss REAL NOT NULL,
              state_q_len INTEGER NOT NULL,
              tau REAL NOT NULL,
              kbits INTEGER NOT NULL,
              reward REAL NOT NULL,
              topic TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS markers (
              ts INTEGER NOT NULL,
              t_recv_ns INTEGER NOT NULL,
              device_id TEXT NOT NULL,
              note TEXT NOT NULL,
              topic TEXT NOT NULL
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_events_ts_ns ON events(ts_ns)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_decisions_ts ON decisions(ts)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_markers_ts ON markers(ts)")
