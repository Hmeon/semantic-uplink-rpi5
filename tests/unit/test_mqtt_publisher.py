from __future__ import annotations

from dataclasses import dataclass

import pytest


@dataclass
class _PublishInfo:
    rc: int
    mid: int | None


class _FakeClient:
    def __init__(
        self,
        *,
        publish_info: _PublishInfo | None = None,
        publish_raises: Exception | None = None,
    ):
        self.publish_info = publish_info or _PublishInfo(rc=0, mid=1)
        self.publish_raises = publish_raises

        self.connect_async_calls: list[tuple[str, int, int]] = []
        self.loop_start_called = 0
        self.loop_stop_called = 0
        self.disconnect_called = 0

        self.on_connect = None
        self.on_disconnect = None
        self.on_publish = None

    def connect_async(self, host: str, port: int, keepalive: int) -> int:
        self.connect_async_calls.append((str(host), int(port), int(keepalive)))
        return 0

    def loop_start(self) -> None:
        self.loop_start_called += 1

    def loop_stop(self) -> None:
        self.loop_stop_called += 1

    def disconnect(self) -> None:
        self.disconnect_called += 1

    def publish(self, *, topic: str, payload: bytes, qos: int, retain: bool) -> _PublishInfo:
        if self.publish_raises is not None:
            raise self.publish_raises
        return self.publish_info


class _DummyOutbox:
    def __init__(self):
        self.acked: list[int] = []
        self.nacked: list[int] = []
        self.reset_calls = 0

    def claim_next(self, *, limit: int):
        return []

    def ack(self, msg_id: int) -> bool:
        self.acked.append(int(msg_id))
        return True

    def nack(self, msg_id: int) -> None:
        self.nacked.append(int(msg_id))

    def requeue_stuck(self) -> int:
        return 0

    def reset_inflight(self) -> int:
        self.reset_calls += 1
        return 0


def test_publisher_start_uses_connect_async(monkeypatch: pytest.MonkeyPatch) -> None:
    from edge.uploader import mqtt_publisher as mp

    outbox = _DummyOutbox()
    fake_client = _FakeClient()
    monkeypatch.setattr(mp.mqtt, "Client", lambda *a, **k: fake_client)

    pub = mp.MQTTPublisher(outbox, broker="localhost", port=1883, keepalive=30)
    pub.start()
    try:
        assert fake_client.connect_async_calls == [("localhost", 1883, 30)]
        assert fake_client.loop_start_called == 1
    finally:
        pub.stop()
        assert fake_client.loop_stop_called == 1
        assert fake_client.disconnect_called == 1


def test_publisher_publish_item_ack_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    from edge.uploader import mqtt_publisher as mp
    from edge.uploader.outbox import OutboxItem

    outbox = _DummyOutbox()
    fake_client = _FakeClient(publish_info=_PublishInfo(rc=mp.mqtt.MQTT_ERR_SUCCESS, mid=123))
    monkeypatch.setattr(mp.mqtt, "Client", lambda *a, **k: fake_client)

    pub = mp.MQTTPublisher(outbox)
    it = OutboxItem(id=7, topic="t", payload=b"{}", qos=1, retain=False, attempts=1, created_ns=1)

    pub._publish_item(it)
    assert pub._mid2oid.get(123) == 7

    pub._on_publish(fake_client, None, 123)
    assert outbox.acked == [7]
    assert 123 not in pub._mid2oid


def test_publisher_publish_non_success_nacks(monkeypatch: pytest.MonkeyPatch) -> None:
    from edge.uploader import mqtt_publisher as mp
    from edge.uploader.outbox import OutboxItem

    outbox = _DummyOutbox()
    fake_client = _FakeClient(publish_info=_PublishInfo(rc=1, mid=None))
    monkeypatch.setattr(mp.mqtt, "Client", lambda *a, **k: fake_client)

    pub = mp.MQTTPublisher(outbox)
    it = OutboxItem(id=9, topic="t", payload=b"{}", qos=1, retain=False, attempts=1, created_ns=1)

    pub._publish_item(it)
    assert outbox.nacked == [9]


def test_publisher_publish_exception_nacks(monkeypatch: pytest.MonkeyPatch) -> None:
    from edge.uploader import mqtt_publisher as mp
    from edge.uploader.outbox import OutboxItem

    outbox = _DummyOutbox()
    fake_client = _FakeClient(publish_raises=RuntimeError("boom"))
    monkeypatch.setattr(mp.mqtt, "Client", lambda *a, **k: fake_client)

    pub = mp.MQTTPublisher(outbox)
    it = OutboxItem(id=11, topic="t", payload=b"{}", qos=1, retain=False, attempts=1, created_ns=1)

    pub._publish_item(it)
    assert outbox.nacked == [11]
