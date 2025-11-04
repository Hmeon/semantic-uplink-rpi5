"""MQTT 연결/발행/구독 헬퍼 스켈레톤."""
from __future__ import annotations
import paho.mqtt.client as mqtt

def make_client(client_id: str, host: str = "localhost", port: int = 1883) -> mqtt.Client:
    cli = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv311, clean_session=False)
    # TODO: username/password, TLS 등 보안 옵션
    cli.connect(host, port, keepalive=60)
    return cli
