"""MQTT 발행 래퍼 스켈레톤 — QoS1/백오프/Outbox 연동."""
from __future__ import annotations

class MQTTPublisher:
    def __init__(self):
        # TODO: MQTT client, Outbox 주입
        pass
    async def publish_event(self, topic: str, payload: bytes, qos: int = 1) -> None:
        """QoS1 발행 → Ack 후 outbox 정리. (TODO)"""
        pass
