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

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

_LOG = logging.getLogger(__name__)
__all__ = ["Outbox", "OutboxItem"]


@dataclass(slots=True)
class OutboxItem:
    """퍼블리셔가 송신에 사용할 레코드(인메모리 표현)."""
    id: int
    topic: str
    payload: bytes
    qos: int
    retain: bool
    attempts: int
    created_ns: int
    last_attempt_ns: int | None = None


class Outbox:
    """
    Durable FIFO outbox (SQLite WAL).
    상태(state): 0=queued, 1=inflight
    - queued: claim 대상 (available_at_ns <= now)
    - inflight: 브로커로 송신 시도 중(ACK 대기)
    """

    def __init__(self,
                 db_path: str,
                 ack_timeout_s: float = 20.0,
                 backoff_base_s: float = 1.0,
                 backoff_cap_s: float = 60.0):
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
        _LOG.info("Outbox opened: %s (ack_timeout_s=%.1f, backoff_base_s=%.1f, cap_s=%.1f)",
                  db_path, ack_timeout_s, backoff_base_s, backoff_cap_s)

    # ---- Compatibility helpers (legacy API expected by GitHub unit tests) ----

    def setup(self) -> None:
        """Ensure the database schema exists (idempotent)."""
        self._bootstrap_db()

    def pending(self) -> int:
        """Return the number of queued or inflight messages."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) FROM messages WHERE status IN (0, 1)")
            (count,) = cur.fetchone()
            cur.close()
        return int(count)

    def mark_done(self, msg_id: int) -> bool:
        """Alias for :meth:`ack` maintained for backwards compatibility."""
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
        """
        새 메시지를 **queued** 상태로 저장하고 id를 반환.
        - payload는 bytes 또는 str(UTF-8) 허용
        - QoS는 {0,1,2} 허용(기본 1; 프로젝트 표준). retain은 bool.
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
        """
        현재 시각 기준 **ready(queued & available_at <= now)**인 메시지들을
        id 오름차순으로 최대 `limit`개까지 **inflight**로 전이(마킹)하고 반환.
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
        """브로커에서 PUBACK(또는 성공 판정)을 받은 메시지 삭제."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("DELETE FROM messages WHERE id=?", (int(msg_id),))
            deleted = cur.rowcount
            cur.execute("COMMIT")
            cur.close()
        return deleted > 0

    def nack(self, msg_id: int) -> None:
        """
        송신 실패/거절 시 호출. 메시지를 **queued**로 되돌리고 지수백오프를 적용한다.
        (브로커 연결 끊김 등 즉시 재시도 의미가 있을 때는 reset_inflight() 사용)
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

    def requeue_stuck(self) -> int:
        """ACK 타임아웃을 초과한 inflight 전부를 재큐잉. 반환: 재큐잉 수."""
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
            cur.execute("COMMIT")
            cur.close()
        return count

    def reset_inflight(self) -> int:
        """
        **브로커 재연결 직후** 호출: inflight를 즉시 재시도 가능하도록 queued로 되돌린다.
        (ACK를 더는 기대할 수 없으므로, available_at=now로 즉시 재시도)
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

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # 컨텍스트 매니저 지원
    def __enter__(self) -> "Outbox":
        return self
    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
