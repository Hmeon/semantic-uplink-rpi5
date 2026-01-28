from __future__ import annotations

import pytest

import common.schema as schema
from common.schema import EventMsg, LinkProfile, PolicyDecisionMsg, PolicyMode, SensorType


def test_event_mqtt_topic_supports_custom_base_topic() -> None:
    ev = EventMsg(
        ts=1,
        seq=1,
        device_id="dev1",
        sensor=SensorType.TEMP,
        val=1.0,
        pred=1.0,
        res=0.0,
        tau=0.2,
        kbits=8,
        profile=LinkProfile.SLOW_10KBPS,
        policy=PolicyMode.FIXED_TAU,
    )
    assert ev.mqtt_topic() == "edge/dev1/temp/event"
    assert ev.mqtt_topic("edge") == "edge/dev1/temp/event"
    assert ev.mqtt_topic("lab/edge") == "lab/edge/dev1/temp/event"
    assert ev.mqtt_topic("/lab/edge/") == "lab/edge/dev1/temp/event"


def test_event_mqtt_topic_rejects_empty_or_wildcards() -> None:
    ev = EventMsg(
        ts=1,
        seq=1,
        device_id="dev1",
        sensor=SensorType.TEMP,
        val=1.0,
        pred=1.0,
        res=0.0,
        tau=0.2,
        kbits=8,
        profile=LinkProfile.SLOW_10KBPS,
        policy=PolicyMode.FIXED_TAU,
    )
    with pytest.raises(ValueError):
        _ = ev.mqtt_topic("")
    with pytest.raises(ValueError):
        _ = ev.mqtt_topic("/")
    with pytest.raises(ValueError):
        _ = ev.mqtt_topic("edge/+")
    with pytest.raises(ValueError):
        _ = ev.mqtt_topic("edge/#")


def test_event_estimated_mqtt_size_uses_base_topic(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def _fake_publish_size(topic: str, payload_len: int, qos: int = 1) -> int:
        seen["topic"] = topic
        seen["payload_len"] = payload_len
        seen["qos"] = qos
        return 123

    monkeypatch.setattr(schema, "mqtt_v311_publish_size", _fake_publish_size)

    ev = EventMsg(
        ts=1,
        seq=1,
        device_id="dev1",
        sensor=SensorType.TEMP,
        val=1.0,
        pred=1.0,
        res=0.0,
        tau=0.2,
        kbits=8,
        profile=LinkProfile.SLOW_10KBPS,
        policy=PolicyMode.FIXED_TAU,
    )
    assert ev.estimated_mqtt_size(qos=1, base_topic="lab/edge") == 123
    assert seen["topic"] == "lab/edge/dev1/temp/event"
    assert seen["qos"] == 1


def test_policy_decision_estimated_mqtt_size_uses_policy_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def _fake_publish_size(topic: str, payload_len: int, qos: int = 1) -> int:
        seen["topic"] = topic
        seen["payload_len"] = payload_len
        seen["qos"] = qos
        return 456

    monkeypatch.setattr(schema, "mqtt_v311_publish_size", _fake_publish_size)

    msg = PolicyDecisionMsg(
        ts=1,
        device_id="dev1",
        state_aoi=1.0,
        state_res=0.0,
        state_res_var=0.0,
        state_loss=0.0,
        state_q_len=0,
        tau=0.2,
        kbits=8,
        reward=0.0,
    )
    assert msg.estimated_mqtt_size(qos=1) == 456
    assert seen["topic"] == "policy/dev1/decision"
    assert seen["qos"] == 1
