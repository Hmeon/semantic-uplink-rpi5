from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class _PublishInfo:
    rc: int
    mid: int | None


class _FakeClient:
    def __init__(self, publish_info: _PublishInfo | None = None):
        self.publish_info = publish_info or _PublishInfo(rc=0, mid=1)
        self.on_connect = None
        self.on_disconnect = None
        self.on_publish = None

    def max_inflight_messages_set(self, _n: int) -> None:
        return None

    def max_queued_messages_set(self, _n: int) -> None:
        return None

    def reconnect_delay_set(self, min_delay: int, max_delay: int) -> None:
        _ = (min_delay, max_delay)

    def connect_async(self, host: str, port: int, keepalive: int) -> int:
        _ = (host, port, keepalive)
        return 0

    def loop_start(self) -> None:
        return None

    def loop_stop(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def publish(self, *, topic: str, payload: bytes, qos: int, retain: bool):
        _ = (topic, payload, qos, retain)
        return self.publish_info


class _Outbox:
    def __init__(self, *, claim_items=None, reset_result: int = 0):
        self.claim_items = list(claim_items or [])
        self.reset_result = int(reset_result)
        self.reset_calls = 0
        self.acked: list[int] = []
        self.requeue_calls = 0
        self.claim_limits: list[int] = []

    def claim_next(self, *, limit: int):
        self.claim_limits.append(int(limit))
        if self.claim_items:
            return [self.claim_items.pop(0)]
        return []

    def ack(self, msg_id: int) -> bool:
        self.acked.append(int(msg_id))
        return True

    def nack(self, msg_id: int) -> None:
        _ = msg_id

    def requeue_stuck(self) -> int:
        self.requeue_calls += 1
        return 1

    def reset_inflight(self) -> int:
        self.reset_calls += 1
        return self.reset_result


def test_connect_disconnect_callbacks_manage_state(monkeypatch: pytest.MonkeyPatch) -> None:
    from edge.uploader import mqtt_publisher as mp
    from edge.uploader.outbox import OutboxItem

    fake_client = _FakeClient()
    outbox = _Outbox(reset_result=3)
    monkeypatch.setattr(mp.mqtt, "Client", lambda *a, **k: fake_client)

    pub = mp.MQTTPublisher(outbox)
    pub._mid2oid[123] = 999

    pub._on_connect(fake_client, None, None, 0)
    assert pub.is_connected() is True
    assert outbox.reset_calls == 1

    pub._on_disconnect(fake_client, None, rc=1)
    assert pub.is_connected() is False
    assert pub._mid2oid == {}

    # Unknown mid should be ignored without ack side-effects.
    pub._on_publish(fake_client, None, 777)
    assert outbox.acked == []

    # Known mid should ack and remove mapping.
    item = OutboxItem(
        id=7,
        topic="edge/dev1/temp/event",
        payload=b"{}",
        qos=1,
        retain=False,
        attempts=1,
        created_ns=1,
    )
    pub._publish_item(item)
    assert pub._mid2oid.get(1) == 7
    pub._on_publish(fake_client, None, 1)
    assert outbox.acked == [7]
    assert 1 not in pub._mid2oid


def test_worker_and_requeue_loops_execute_single_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from edge.uploader import mqtt_publisher as mp
    from edge.uploader.outbox import OutboxItem

    item = OutboxItem(
        id=5,
        topic="edge/dev1/temp/event",
        payload=b"{}",
        qos=1,
        retain=False,
        attempts=1,
        created_ns=1,
    )
    outbox = _Outbox(claim_items=[item])
    fake_client = _FakeClient()
    monkeypatch.setattr(mp.mqtt, "Client", lambda *a, **k: fake_client)

    pub = mp.MQTTPublisher(outbox, max_inflight=1, claim_batch=10, requeue_period_s=1)
    pub._connected = True

    # Stop after first publish to avoid infinite loop.
    real_publish_item = pub._publish_item

    def _publish_and_stop(it):
        real_publish_item(it)
        pub._stop.set()

    monkeypatch.setattr(pub, "_publish_item", _publish_and_stop)
    pub._worker_loop()
    assert outbox.claim_limits == [1]  # budget=min(claim_batch, max_inflight)

    # Requeue loop: stop after first call.
    pub._stop.clear()

    def _requeue_once() -> int:
        outbox.requeue_calls += 1
        pub._stop.set()
        return 1

    monkeypatch.setattr(outbox, "requeue_stuck", _requeue_once)
    pub._requeue_loop()
    assert outbox.requeue_calls == 1

