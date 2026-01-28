# edge/uploader/outbox.py
# Python 3.10+
# 목적: MQTT 퍼블리셔와 분리된 **디스크 영속 Outbox**.
# - 오프라인 내성: enqueue()는 즉시 SQLite WAL에 커밋(유실 0 지향)
# - 순서 보존: claim_next()가 항상 id ASC로 배출
# - QoS1과 정합: ack()가 와야 삭제. nack()/timeout 시 재큐잉(최소 중복)
# - 제어: 지수 백오프(backoff_base_s, cap), ACK 타임아웃(ack_timeout_s), 재연결 시 reset_inflight()
# - 단순/명확: MQTT 세부는 mqtt_publisher.py에서 처리(본 모듈은 큐 책임만)
#
# 스키마/토픽/지표/DoD는 과제 동결안과 일치합니다. (유실 0, QoS1 중복 제거는 collector에서 검증)  # noqa

"""Durable SQLite-backed outbox for QoS1 publishing.

Provides enqueue/claim/ack/nack semantics with backoff and timeout handling to
survive disconnects. The queue is thread-safe within a process and relies on
SQLite WAL for durability.
"""

from __future__ import annotations

import logging
import math
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import List

_LOG = logging.getLogger(__name__)
__all__ = ["Outbox", "OutboxItem", "DeliveryStats"]


@dataclass(slots=True)
class OutboxItem:
    """In-memory representation of a queued MQTT publish.

    Args:
        id: SQLite row id for the message.
        topic: MQTT topic string.
        payload: Message payload bytes.
        qos: MQTT QoS level.
        retain: MQTT retain flag.
        attempts: Number of publish attempts so far.
        created_ns: Enqueue timestamp in nanoseconds.
        last_attempt_ns: Timestamp of the last attempt in nanoseconds.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Mirrors the `messages` table row state.

    Failure Modes:
        - None.
    """
    id: int
    topic: str
    payload: bytes
    qos: int
    retain: bool
    attempts: int
    created_ns: int
    last_attempt_ns: int | None = None


@dataclass(slots=True)
class DeliveryStats:
    """Snapshot of delivery/ack latency and loss EWMAs.

    Args:
        ack_latency_ms: Last observed ACK latency in milliseconds.
        ack_latency_ewma_ms: EWMA of ACK latency in milliseconds.
        loss_ewma: EWMA of loss indicators.
        acked: Count of acknowledged messages.
        nacked: Count of negatively acknowledged messages.
        timeouts: Count of ACK timeouts.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Values reflect in-memory counters since process start.

    Failure Modes:
        - None.
    """
    ack_latency_ms: float | None
    ack_latency_ewma_ms: float | None
    loss_ewma: float
    acked: int
    nacked: int
    timeouts: int


class Outbox:
    """Durable FIFO outbox backed by SQLite WAL.

    Args:
        db_path: Path to the SQLite database file.
        ack_timeout_s: ACK timeout window in seconds.
        backoff_base_s: Base backoff delay in seconds.
        backoff_cap_s: Maximum backoff delay in seconds.
        ack_latency_alpha: EWMA alpha for ACK latency.
        loss_ewma_alpha: EWMA alpha for loss estimation.

    Returns:
        None.

    Raises:
        ValueError: If db_path is empty.
        OSError: If the database directory cannot be created.
        sqlite3.Error: If the database cannot be opened or initialized.

    Contract:
        - Status 0=queued, 1=inflight; only queued rows are claimable.
        - A message is removed only after ACK (`ack`), never on publish attempt.

    Side Effects:
        - Writes to SQLite on every enqueue/ack/nack.

    Failure Modes:
        - DB errors propagate to caller; caller should treat failures as non-delivery.
    """

    def __init__(self,
                 db_path: str,
                 ack_timeout_s: float = 20.0,
                 backoff_base_s: float = 1.0,
                 backoff_cap_s: float = 60.0,
                 ack_latency_alpha: float = 0.2,
                 loss_ewma_alpha: float = 0.2):
        if not db_path:
            raise ValueError("db_path is required")
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)

        self.ack_timeout_ns = int(ack_timeout_s * 1e9)
        self.backoff_base_ns = int(max(0.05, backoff_base_s) * 1e9)
        self.backoff_cap_ns = int(max(backoff_base_s, backoff_cap_s) * 1e9)

        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            db_path,
            timeout=30.0,
            isolation_level=None,        # autocommit; 트랜잭션은 명시적 BEGIN
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._bootstrap_db()
        # Delivery/feedback stats (adaptive policy input).
        self._ack_latency_ms_last: float | None = None
        self._ack_latency_ms_ewma: float | None = None
        self._ack_latency_alpha = _clamp_alpha(ack_latency_alpha)
        self._loss_ewma: float = 0.0
        self._loss_alpha = _clamp_alpha(loss_ewma_alpha)
        self._loss_samples = 0
        self._acked = 0
        self._nacked = 0
        self._timeouts = 0
        _LOG.info("Outbox opened: %s (ack_timeout_s=%.1f, backoff_base_s=%.1f, cap_s=%.1f)",
                  db_path, ack_timeout_s, backoff_base_s, backoff_cap_s)

    # ---- Compatibility helpers (legacy API expected by GitHub unit tests) ----

    def setup(self) -> None:
        """Ensure the database schema exists (idempotent).

        Args:
            None.

        Returns:
            None.

        Raises:
            sqlite3.Error: If schema creation fails.

        Side Effects:
            - Creates tables/indexes in the SQLite file.

        Contract:
            - Safe to call multiple times.

        Failure Modes:
            - DB errors propagate to caller.
        """
        self._bootstrap_db()

    def pending(self) -> int:
        """Return the number of queued or inflight messages.

        Args:
            None.

        Returns:
            Count of rows with status in (queued, inflight).

        Raises:
            sqlite3.Error: If the count query fails.

        Side Effects:
            - Reads from the SQLite database.

        Contract:
            - Counts only statuses 0 and 1.

        Failure Modes:
            - DB errors propagate to caller.
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) FROM messages WHERE status IN (0, 1)")
            (count,) = cur.fetchone()
            cur.close()
        return int(count)

    def mark_done(self, msg_id: int) -> bool:
        """Alias for `ack`, kept for backward compatibility.

        Args:
            msg_id: Outbox row id to acknowledge.

        Returns:
            True if a row was removed, False otherwise.

        Raises:
            sqlite3.Error: If the delete fails.

        Side Effects:
            - Deletes the row and updates ACK/loss statistics.

        Contract:
            - Equivalent to calling `ack(msg_id)`.

        Failure Modes:
            - DB errors propagate to caller; message may be retried.
        """
        return self.ack(int(msg_id))

    # ---------------- 내부: DB 구성 ----------------

    def _bootstrap_db(self) -> None:
        c = self._conn.cursor()
        # 내구성/동시성: WAL + synchronous=FULL (전원 차단 시에도 커밋 보존 지향)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=FULL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            topic            TEXT    NOT NULL,
            payload          BLOB    NOT NULL,
            qos              INTEGER NOT NULL,
            retain           INTEGER NOT NULL,
            status           INTEGER NOT NULL,     -- 0=queued, 1=inflight
            attempts         INTEGER NOT NULL DEFAULT 0,
            available_at_ns  INTEGER NOT NULL,
            last_attempt_ns  INTEGER,
            created_ns       INTEGER NOT NULL
        );
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_q ON messages(status, available_at_ns, id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_i ON messages(status, last_attempt_ns)")
        c.close()

    # ---------------- 공용 API ----------------

    def enqueue(self, topic: str, payload: bytes | str, qos: int = 1,
                retain: bool = False, created_ns: int | None = None) -> int:
        """Persist a new queued message and return its row id.

        Args:
            topic: MQTT topic name (non-empty).
            payload: Payload bytes or UTF-8 string.
            qos: MQTT QoS level (0/1/2).
            retain: MQTT retain flag.
            created_ns: Optional creation timestamp override.

        Returns:
            New row id for the queued message.

        Raises:
            ValueError: If topic is empty or QoS is invalid.
            sqlite3.Error: If the insert fails.

        Side Effects:
            - Writes a new row to SQLite with WAL durability.

        Contract:
            - Inserts in queued state (status=0).

        Failure Modes:
            - DB errors propagate to caller; caller should treat as not enqueued.
        """
        if not isinstance(topic, str) or not topic:
            raise ValueError("topic must be non-empty str")
        if qos not in (0, 1, 2):
            raise ValueError("qos must be 0, 1, or 2")

        if isinstance(payload, str):
            payload_b = payload.encode("utf-8")
        else:
            payload_b = bytes(payload)

        now_ns = time.time_ns() if created_ns is None else int(created_ns)

        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute(
                "INSERT INTO messages(topic, payload, qos, retain, status, attempts, available_at_ns, created_ns) "
                "VALUES (?, ?, ?, ?, 0, 0, ?, ?)",
                (topic, sqlite3.Binary(payload_b), int(qos), int(bool(retain)), now_ns, now_ns),
            )
            mid = int(cur.lastrowid)
            cur.execute("COMMIT")
            cur.close()
        return mid

    def claim_next(self, limit: int = 1) -> List[OutboxItem]:
        """Claim ready messages and mark them inflight.

        Args:
            limit: Maximum number of rows to claim.

        Returns:
            List of OutboxItem instances marked inflight.

        Raises:
            sqlite3.Error: If selection or update fails.

        Side Effects:
            - Requeues timed-out inflight rows with backoff.
            - Marks claimed rows as inflight and increments attempts.

        Contract:
            - Returns items ordered by ascending id (FIFO).

        Failure Modes:
            - DB errors propagate to caller; caller should retry later.
        """
        if limit <= 0:
            return []
        now_ns = time.time_ns()

        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            # 1) ACK 타임아웃 초과 inflight → queued 재큐잉 (지수백오프)
            cur.execute("""
                SELECT id, attempts, last_attempt_ns
                FROM messages
                WHERE status=1 AND last_attempt_ns IS NOT NULL AND (? - last_attempt_ns) >= ?
            """, (now_ns, self.ack_timeout_ns))
            rows_timeout = cur.fetchall()
            for r in rows_timeout:
                mid = int(r["id"]); attempts = int(r["attempts"])
                delay_ns = min(self.backoff_cap_ns, self.backoff_base_ns * (2 ** max(0, attempts - 1)))
                cur.execute(
                    "UPDATE messages SET status=0, available_at_ns=?, last_attempt_ns=NULL WHERE id=?",
                    (now_ns + int(delay_ns), mid),
                )

            # 2) Ready 집합 선택
            cur.execute("""
                SELECT id, topic, payload, qos, retain, attempts, created_ns
                FROM messages
                WHERE status=0 AND available_at_ns <= ?
                ORDER BY id ASC
                LIMIT ?
            """, (now_ns, int(limit)))
            rows = cur.fetchall()
            ids = [int(r["id"]) for r in rows]

            # 3) inflight 마킹(+attempts, last_attempt_ns)
            if ids:
                idlist = ",".join("?" for _ in ids)
                cur.execute(
                    f"UPDATE messages SET status=1, attempts=attempts+1, last_attempt_ns=? WHERE id IN ({idlist})",
                    (now_ns, *ids)
                )
            cur.execute("COMMIT")
            cur.close()

        items: List[OutboxItem] = []
        for r in rows:
            items.append(
                OutboxItem(
                    id=int(r["id"]),
                    topic=str(r["topic"]),
                    payload=bytes(r["payload"]),
                    qos=int(r["qos"]),
                    retain=bool(r["retain"]),
                    # attempts는 위 UPDATE로 +1 되었으니 +1 반영
                    attempts=int(r["attempts"]) + 1,
                    created_ns=int(r["created_ns"]),
                    last_attempt_ns=now_ns,
                )
            )
        return items

    def ack(self, msg_id: int) -> bool:
        """Acknowledge delivery and remove a message from the outbox.

        Args:
            msg_id: Outbox row id to acknowledge.

        Returns:
            True if a row was deleted, False if not found.

        Raises:
            sqlite3.Error: If the delete fails.

        Side Effects:
            - Deletes the row and updates ACK latency/loss statistics.

        Contract:
            - Idempotent for already-acked ids (returns False).

        Failure Modes:
            - DB errors propagate to caller; message may be retried.
        """
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            row = cur.execute(
                "SELECT created_ns, topic FROM messages WHERE id=?",
                (int(msg_id),),
            ).fetchone()
            cur.execute("DELETE FROM messages WHERE id=?", (int(msg_id),))
            deleted = cur.rowcount
            cur.execute("COMMIT")
            cur.close()
            if deleted > 0:
                self._acked += 1
                self._update_loss_ewma(0.0)
                if row is not None:
                    topic = str(row["topic"])
                    # Track ACK latency EWMA only for sensor events.
                    # Topic format is `{base_topic}/{device_id}/{sensor}/event` where `base_topic`
                    # may be customized via device config.
                    parts = topic.split("/")
                    if len(parts) >= 4 and parts[-1] == "event":
                        created_ns = int(row["created_ns"])
                        latency_ms = (time.time_ns() - created_ns) / 1e6
                        if math.isfinite(latency_ms) and latency_ms >= 0.0:
                            self._ack_latency_ms_last = float(latency_ms)
                            if self._ack_latency_ms_ewma is None:
                                self._ack_latency_ms_ewma = float(latency_ms)
                            else:
                                self._ack_latency_ms_ewma = (
                                    (1.0 - self._ack_latency_alpha) * float(self._ack_latency_ms_ewma)
                                    + self._ack_latency_alpha * float(latency_ms)
                                )
        return deleted > 0

    def nack(self, msg_id: int) -> None:
        """Requeue a message with backoff after a publish failure.

        Args:
            msg_id: Outbox row id to requeue.

        Returns:
            None.

        Raises:
            sqlite3.Error: If the update fails.

        Side Effects:
            - Moves the row back to queued with exponential backoff.
            - Updates loss EWMA counters.

        Contract:
            - Intended for publish failures; use `reset_inflight` on reconnect.

        Failure Modes:
            - DB errors propagate to caller; message remains inflight or queued.
        """
        now_ns = time.time_ns()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            row = cur.execute("SELECT attempts FROM messages WHERE id=?", (int(msg_id),)).fetchone()
            if row is None:
                cur.execute("COMMIT"); cur.close(); return
            attempts = int(row["attempts"])
            delay_ns = min(self.backoff_cap_ns, self.backoff_base_ns * (2 ** max(0, attempts - 1)))
            cur.execute(
                "UPDATE messages SET status=0, available_at_ns=?, last_attempt_ns=NULL WHERE id=?",
                (now_ns + int(delay_ns), int(msg_id)),
            )
            cur.execute("COMMIT")
            cur.close()
            self._nacked += 1
            self._update_loss_ewma(1.0)

    def requeue_stuck(self) -> int:
        """Requeue inflight messages that exceeded ACK timeout.

        Args:
            None.

        Returns:
            Number of messages requeued.

        Raises:
            sqlite3.Error: If the update fails.

        Side Effects:
            - Updates queue state and applies backoff.

        Contract:
            - Uses ACK timeout as the only stuck criterion.

        Failure Modes:
            - DB errors propagate to caller.
        """
        now_ns = time.time_ns()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("""
                SELECT id, attempts FROM messages
                WHERE status=1 AND last_attempt_ns IS NOT NULL AND (? - last_attempt_ns) >= ?
            """, (now_ns, self.ack_timeout_ns))
            rows = cur.fetchall()
            count = 0
            for r in rows:
                mid = int(r["id"]); attempts = int(r["attempts"])
                delay_ns = min(self.backoff_cap_ns, self.backoff_base_ns * (2 ** max(0, attempts - 1)))
                cur.execute(
                    "UPDATE messages SET status=0, available_at_ns=?, last_attempt_ns=NULL WHERE id=?",
                    (now_ns + int(delay_ns), mid),
                )
                count += 1
                self._timeouts += 1
                self._update_loss_ewma(1.0)
            cur.execute("COMMIT")
            cur.close()
        return count

    def reset_inflight(self) -> int:
        """Reset inflight messages to queued after reconnect.

        Args:
            None.

        Returns:
            Number of rows moved back to queued.

        Raises:
            sqlite3.Error: If the update fails.

        Side Effects:
            - Moves all inflight rows to queued with immediate availability.

        Contract:
            - Intended for use right after broker reconnect.

        Failure Modes:
            - DB errors propagate to caller.
        """
        now_ns = time.time_ns()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("UPDATE messages SET status=0, available_at_ns=?, last_attempt_ns=NULL WHERE status=1",
                        (now_ns,))
            affected = cur.rowcount
            cur.execute("COMMIT")
            cur.close()
        return int(affected)

    def stats(self) -> dict:
        """Return queue size counters for monitoring.

        Args:
            None.

        Returns:
            Dict with queued/inflight/total counts and next_id.

        Raises:
            sqlite3.Error: If the query fails.

        Side Effects:
            - Reads from the SQLite database.

        Contract:
            - Counts reflect current DB state (not including in-memory buffers).

        Failure Modes:
            - DB errors propagate to caller.
        """
        with self._lock:
            cur = self._conn.cursor()
            q = cur.execute("""
                SELECT
                  SUM(CASE WHEN status=0 THEN 1 ELSE 0 END) AS queued,
                  SUM(CASE WHEN status=1 THEN 1 ELSE 0 END) AS inflight,
                  COUNT(*) AS total,
                  MIN(CASE WHEN status=0 THEN id END) AS next_id
                FROM messages
            """)
            row = q.fetchone()
            cur.close()
        row = row or {"queued": 0, "inflight": 0, "total": 0, "next_id": None}
        return {k: (int(v) if v is not None else 0) for k, v in dict(row).items()}

    def delivery_stats(self) -> DeliveryStats:
        """Return delivery/ack statistics used by adaptive policies.

        Args:
            None.

        Returns:
            DeliveryStats snapshot of ACK latency and loss EWMAs.

        Raises:
            None.

        Side Effects:
            - None.

        Contract:
            - Counters are process-local since startup.

        Failure Modes:
            - None.
        """
        with self._lock:
            return DeliveryStats(
                ack_latency_ms=self._ack_latency_ms_last,
                ack_latency_ewma_ms=self._ack_latency_ms_ewma,
                loss_ewma=float(self._loss_ewma),
                acked=int(self._acked),
                nacked=int(self._nacked),
                timeouts=int(self._timeouts),
            )

    def _update_loss_ewma(self, sample: float) -> None:
        sample_val = 1.0 if float(sample) > 0 else 0.0
        if self._loss_samples <= 0 or not math.isfinite(self._loss_ewma):
            self._loss_ewma = float(sample_val)
        else:
            self._loss_ewma = (
                (1.0 - self._loss_alpha) * float(self._loss_ewma)
                + self._loss_alpha * float(sample_val)
            )
        if not math.isfinite(self._loss_ewma):
            self._loss_ewma = float(sample_val)
        self._loss_ewma = float(min(1.0, max(0.0, self._loss_ewma)))
        self._loss_samples += 1

    def close(self) -> None:
        """Close the underlying SQLite connection.

        Args:
            None.

        Returns:
            None.

        Raises:
            None.

        Side Effects:
            - Closes the database handle; further calls will fail.

        Contract:
            - Safe to call multiple times.

        Failure Modes:
            - Close errors are suppressed to avoid masking shutdown.
        """
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # 컨텍스트 매니저 지원
    def __enter__(self) -> "Outbox":
        """Context manager entry; returns self."""
        return self
    def __exit__(self, exc_type, exc, tb) -> None:
        """Context manager exit; closes the database connection."""
        self.close()


def _clamp_alpha(value: float, default: float = 0.2) -> float:
    try:
        v = float(value)
    except Exception:
        return float(default)
    if not math.isfinite(v) or v <= 0.0:
        return float(default)
    return float(min(1.0, v))
