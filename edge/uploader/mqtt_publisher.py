# edge/uploader/mqtt_publisher.py
# Python 3.10+
# 목적: Outbox(디스크 큐)와 paho-mqtt 퍼블리셔를 결합해 QoS1 보장, 재연결/백오프, 유실 0을 달성.
# - 송신 순서 보존(id ASC), PUBACK 기반 삭제(ack), 실패/타임아웃 재큐잉(nack/requeue_stuck)
# - 브로커 단절 시 inflight 전량을 즉시 queued로 복귀(reset_inflight) → 재연결 즉시 재시도
# - paho 내부 재연결(backoff) + Outbox 자체 지수 백오프(이중 안전장치)
# - 보안 옵션(TLS/username/password)은 선택 지원.
# - 본 모듈은 "퍼블리시"만 책임, 메시지 생성/스키마/크기 산정은 상위 모듈 담당.  # noqa

from __future__ import annotations

import argparse
import os
import signal
import ssl
import sys
import threading
import time
from typing import Dict, Optional

import paho.mqtt.client as mqtt

from edge.uploader.outbox import Outbox, OutboxItem


class MQTTPublisher:
    """
    Durable MQTT publisher with QoS1 and offline-first behavior.
    - Outbox로부터 배치(claim_batch) 단위로 메시지를 인출하여 publish.
    - paho on_publish(mid) → outbox.ack(msg_id) 매핑 관리.
    - 연결 끊김/실패 시 outbox.nack() 또는 reset_inflight()로 재시도 대기열 복귀.
    """

    def __init__(
        self,
        outbox: Outbox,
        *,
        broker: str = "localhost",
        port: int = 1883,
        client_id: str = "edge-pub",
        keepalive: int = 30,
        username: str | None = None,
        password: str | None = None,
        tls: bool = False,
        cafile: str | None = None,
        certfile: str | None = None,
        keyfile: str | None = None,
        max_inflight: int = 10,
        claim_batch: int = 10,
        requeue_period_s: int = 5,
    ):
        if max_inflight <= 0 or claim_batch <= 0:
            raise ValueError("max_inflight and claim_batch must be > 0")
        self.outbox = outbox
        self.broker = broker
        self.port = int(port)
        self.client_id = client_id
        self.keepalive = int(keepalive)
        self.username = username
        self.password = password
        self.tls = bool(tls)
        self.cafile = cafile
        self.certfile = certfile
        self.keyfile = keyfile
        self.max_inflight = int(max_inflight)
        self.claim_batch = int(claim_batch)
        self.requeue_period_s = int(requeue_period_s)

        # paho client
        self._cli = mqtt.Client(client_id=self.client_id, clean_session=True, protocol=mqtt.MQTTv311)
        # 조정: 내부 인플라이트/큐 크기(보수적)
        try:
            self._cli.max_inflight_messages_set(max_inflight)
            self._cli.max_queued_messages_set(max(2 * claim_batch, 20))
        except Exception:
            pass
        self._cli.on_connect = self._on_connect
        self._cli.on_disconnect = self._on_disconnect
        self._cli.on_publish = self._on_publish
        # 자동 재연결 backoff
        try:
            self._cli.reconnect_delay_set(min_delay=1, max_delay=60)
        except Exception:
            pass

        if self.username:
            self._cli.username_pw_set(self.username, self.password)
        if self.tls:
            ctx = ssl.create_default_context(cafile=self.cafile)
            if self.certfile and self.keyfile:
                ctx.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
            self._cli.tls_set_context(ctx)

        # 런타임 상태
        self._connected = False
        self._stop = threading.Event()
        self._worker_th: Optional[threading.Thread] = None
        self._requeue_th: Optional[threading.Thread] = None

        # mid → outbox_id 매핑
        self._mid2oid: Dict[int, int] = {}
        self._map_lock = threading.Lock()

    # ------------- MQTT 콜백 -------------

    def _on_connect(self, client: mqtt.Client, userdata, flags, rc, properties=None):
        if rc == 0:
            self._connected = True
            # 재연결 직후 inflight 복구: 더는 PUBACK을 기대할 수 없으니 즉시 재시도 가능 상태로
            try:
                restored = self.outbox.reset_inflight()
                print(f"[pub] connected to mqtt://{self.broker}:{self.port} (restored {restored} msgs)")
            except Exception as e:
                print(f"[pub] reset_inflight error: {e}")
        else:
            print(f"[pub] connect failed rc={rc}")

    def _on_disconnect(self, client: mqtt.Client, userdata, rc, properties=None):
        self._connected = False
        # 매핑 초기화(어차피 재연결 후 재시도)
        with self._map_lock:
            self._mid2oid.clear()
        print(f"[pub] disconnected rc={rc}")

    def _on_publish(self, client: mqtt.Client, userdata, mid: int):
        # PUBACK 수신 → outbox.ack
        with self._map_lock:
            oid = self._mid2oid.pop(mid, None)
        if oid is not None:
            try:
                self.outbox.ack(oid)
                # 진단용 출력(필요 시 로그 레벨 조절)
                # print(f"[pub] ack mid={mid} oid={oid}")
            except Exception as e:
                print(f"[pub] ack error for oid={oid}: {e}")

    # ------------- 라이프사이클 -------------

    def start(self):
        # MQTT I/O 루프 시작
        self._cli.connect(self.broker, self.port, keepalive=self.keepalive)
        self._cli.loop_start()

        # 작업 스레드: (1) 송신 워커 (2) stuck 재큐잉 워커
        self._worker_th = threading.Thread(target=self._worker_loop, name="mqtt-publisher", daemon=True)
        self._worker_th.start()

        self._requeue_th = threading.Thread(target=self._requeue_loop, name="mqtt-requeue", daemon=True)
        self._requeue_th.start()

        print("[pub] started. Press Ctrl+C to stop.")

    def stop(self):
        if self._stop.is_set():
            return
        self._stop.set()
        try:
            self._cli.loop_stop()
        except Exception:
            pass
        try:
            self._cli.disconnect()
        except Exception:
            pass
        if self._worker_th and self._worker_th.is_alive():
            self._worker_th.join(timeout=2.0)
        if self._requeue_th and self._requeue_th.is_alive():
            self._requeue_th.join(timeout=2.0)
        print("[pub] stopped.")

    # ------------- 내부 워커 -------------

    def _worker_loop(self):
        """
        연결 시: 가용한 인플라이트 여유만큼 Outbox→publish.
        비연결 시: 잠깐 대기(Outbox는 디스크 큐이므로 안전).
        publish 실패(rc!=0) 즉시 nack 후 짧은 sleep.
        """
        sleep_s = 0.05
        while not self._stop.is_set():
            if not self._connected:
                time.sleep(0.2)
                continue

            # 인플라이트 여유 계산
            with self._map_lock:
                inflight_local = len(self._mid2oid)
            budget = max(0, self.max_inflight - inflight_local)
            if budget <= 0:
                time.sleep(sleep_s)
                continue

            # Outbox에서 ready 메시지 인출
            try:
                batch = self.outbox.claim_next(limit=min(self.claim_batch, budget))
            except Exception as e:
                print(f"[pub] claim_next error: {e}")
                time.sleep(0.5)
                continue

            if not batch:
                time.sleep(sleep_s)
                continue

            # 송신
            for item in batch:
                self._publish_item(item)

    def _publish_item(self, it: OutboxItem):
        """단일 레코드를 MQTT로 송신. 큐/매핑/에러 처리는 여기서 일원화."""
        try:
            info = self._cli.publish(
                topic=it.topic,
                payload=it.payload,
                qos=it.qos,
                retain=it.retain,
            )
        except Exception as e:
            # 네트워크/클라이언트 예외 → 즉시 nack 후 짧은 대기
            try:
                self.outbox.nack(it.id)
            except Exception:
                pass
            print(f"[pub] publish exception for id={it.id}: {e}")
            time.sleep(0.2)
            return

        # paho는 큐에 성공적으로 적재되면 rc==MQTT_ERR_SUCCESS(0)과 mid 부여
        rc = getattr(info, "rc", -1)
        mid = getattr(info, "mid", None)
        if rc != mqtt.MQTT_ERR_SUCCESS or mid is None:
            # 즉시 재큐잉(Outbox 백오프 규칙 적용)
            try:
                self.outbox.nack(it.id)
            except Exception:
                pass
            print(f"[pub] publish rc={rc} mid={mid} → nack id={it.id}")
            time.sleep(0.05)
            return

        # mid↔outbox id 매핑(ACK에서 삭제)
        with self._map_lock:
            self._mid2oid[mid] = it.id

    def _requeue_loop(self):
        """주기적으로 ACK 타임아웃 초과 inflight를 재큐잉."""
        while not self._stop.is_set():
            try:
                n = self.outbox.requeue_stuck()
                if n:
                    print(f"[pub] requeued stuck: {n}")
            except Exception as e:
                print(f"[pub] requeue_stuck error: {e}")
            # 주기 슬립
            time.sleep(max(1, self.requeue_period_s))


# ---------------------- CLI ----------------------

def _install_signal_handlers(p: MQTTPublisher):
    def _h(signum, frame):
        print(f"[pub] signal={signum} -> stopping...")
        p.stop()
        sys.exit(0)
    signal.signal(signal.SIGINT, _h)
    signal.signal(signal.SIGTERM, _h)


def main():
    ap = argparse.ArgumentParser(description="Outbox-backed MQTT QoS1 publisher")
    ap.add_argument("--outbox", required=True, help="SQLite path (e.g., artifacts/<run>/outbox.sqlite)")
    ap.add_argument("--broker", default="localhost")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--client-id", default="edge-pub")
    ap.add_argument("--keepalive", type=int, default=30)
    ap.add_argument("--username", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--tls", action="store_true")
    ap.add_argument("--cafile", default=None)
    ap.add_argument("--certfile", default=None)
    ap.add_argument("--keyfile", default=None)
    ap.add_argument("--max-inflight", type=int, default=10)
    ap.add_argument("--claim-batch", type=int, default=10)
    ap.add_argument("--requeue-period", type=int, default=5)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(args.outbox) or ".", exist_ok=True)
    ob = Outbox(args.outbox)

    pub = MQTTPublisher(
        ob,
        broker=args.broker,
        port=args.port,
        client_id=args.client_id,
        keepalive=args.keepalive,
        username=args.username,
        password=args.password,
        tls=args.tls,
        cafile=args.cafile,
        certfile=args.certfile,
        keyfile=args.keyfile,
        max_inflight=args.max_inflight,
        claim_batch=args.claim_batch,
        requeue_period_s=args.requeue_period,
    )
    _install_signal_handlers(pub)
    try:
        pub.start()
        # 메인 스레드는 단순 대기
        while True:
            time.sleep(1.0)
    finally:
        pub.stop()


if __name__ == "__main__":
    main()
