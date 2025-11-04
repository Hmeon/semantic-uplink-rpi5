"""SQLite 기반 Outbox 큐 스켈레톤."""
from __future__ import annotations

class Outbox:
    def __init__(self, path: str = "outbox.db"):
        self.path = path
    async def setup(self) -> None:
        """TODO: 테이블 생성 (id, topic, payload, qos, ts, status)."""
        pass
    async def enqueue(self, topic: str, payload: str, qos: int = 1, ts: float | None = None):
        """TODO: pending 행 추가."""
        pass
    async def pending(self):
        """TODO: pending 행 이터레이터."""
        yield from ()
    async def mark_done(self, oid: int):
        """TODO: status=done 업데이트."""
        pass
