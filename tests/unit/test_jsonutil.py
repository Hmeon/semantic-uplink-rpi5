from __future__ import annotations

from common.jsonutil import dumps, loads
from common.schema import EventMsg, LinkProfile, PolicyDecisionMsg, PolicyMode, SensorType


def test_jsonutil_roundtrip_dict() -> None:
    obj = {"a": 1, "b": "가나다", "c": [1, 2, 3], "d": {"x": 0.5}}
    payload = dumps(obj)
    assert isinstance(payload, (bytes, bytearray))
    decoded = loads(bytes(payload))
    assert decoded == obj


def test_schema_roundtrip_event_and_decision_json() -> None:
    ev = EventMsg(
        ts=123,
        seq=7,
        device_id="dev1",
        sensor=SensorType.TEMP,
        val=1.0,
        pred=0.5,
        res=0.5,
        tau=0.2,
        kbits=8,
        profile=LinkProfile.SLOW_10KBPS,
        policy=PolicyMode.FIXED_TAU,
        aoi_ms=None,
    )
    ev2 = EventMsg.from_json_bytes(ev.to_json_bytes())
    assert ev2 == ev

    dec = PolicyDecisionMsg(
        ts=456,
        device_id="dev1",
        state_aoi=10.0,
        state_res=0.1,
        state_res_var=0.0,
        state_loss=0.0,
        state_q_len=3,
        tau=0.2,
        kbits=8,
        reward=-1.23,
    )
    dec2 = PolicyDecisionMsg.from_json_bytes(dec.to_json_bytes())
    assert dec2 == dec

