from __future__ import annotations

import sqlite3
from pathlib import Path

from collector.store_sqlite import ensure_schema


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def test_ensure_schema_creates_sqlite_file_and_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "collector.sqlite"
    ensure_schema(db_path)
    assert db_path.exists()

    with sqlite3.connect(db_path) as con:
        assert _table_exists(con, "events")
        assert _table_exists(con, "decisions")
        assert _table_exists(con, "markers")

        indexes = {
            str(row[0])
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        assert "idx_events_ts_ns" in indexes
        assert "idx_decisions_ts" in indexes
        assert "idx_markers_ts" in indexes


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.sqlite"
    ensure_schema(db_path)
    ensure_schema(db_path)

    with sqlite3.connect(db_path) as con:
        # If schema creation were not idempotent, this smoke insert/select path
        # would usually fail due to conflicting DDL side effects.
        con.execute(
            """
            INSERT INTO markers (ts, t_recv_ns, device_id, note, topic)
            VALUES (1, 2, 'dev1', 'ok', 'marker/dev1')
            """
        )
        count = con.execute("SELECT COUNT(*) FROM markers").fetchone()[0]
    assert int(count) == 1

