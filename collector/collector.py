# collector/collector.py
# Python 3.10+
# 외부 의존: paho-mqtt, pandas, pyarrow
# 내부 의존: common.mqttutil.mqtt_v311_publish_size (이전 단계에 합의된 함수)

from __future__ import annotations
import argparse
import json
import os
import signal
import threading
import time
from dataclasses import dataclass
from typing import Dict, Tuple, Any, List

import pandas as pd
import paho.mqtt.client as mqtt

# MQTT PUBLISH 패킷 크기 계산(브로커 수신 기준; 헤더 포함)
try:
    from common.mqttutil import mqtt_v311_publish_size
except Exception:
    # 비상 폴백(동일 로직 축약 버전) — 다른 파일은 수정하지 않기 위해 내부에만 둠.
    def _encode_remaining_length(x: int) -> int:
        n = 0
        while True:
            n += 1
            x //= 128
            if x == 0:
                break
        return n
    def mqtt_v311_publish_size(topic: str, payload_len: int, qos: int = 1,
                               dup: bool = False, retain: bool = False) -> int:
        var_header = 2 + len(topic.encode("utf-8")) + (2 if qos > 0 else 0)
        remaining = var_header + payload_len
        fixed = 1 + _encode_remaining_length(remaining)
        return fixed + remaining


@dataclass
class Config:
    run_dir: str
    broker: str = "localhost"
    port: int = 1883
    flush_interval_s: int = 10
    client_id: str = "collector"
    clock_offset_ns: int = 0   # (옵션) edge→collector 보정치. 검증 단계에서는 0.


class Collector:
    """
    QoS1 중복 제거(seq 기반), AoI 계산(실시간 로깅용), Parquet 저장.
    스키마/폴더 규칙은 VALIDATION.md 고정안과 동일.
    """
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = threading.Lock()

        # dedup된 이벤트를 key→row(dict)로 축적
        self._events: Dict[Tuple[str, str, int], Dict[str, Any]] = {}
        # policy decisions는 전부 저장(중복 처리 불필요)
        self._decisions: List[Dict[str, Any]] = []

        # 통계/메타
        self._bytes_total = 0
        self._dup_messages = 0
        self._dup_bytes = 0
        self._first_ns = time.time_ns()
        self._last_ns = self._first_ns

        # MQTT
        self._client: mqtt.Client | None = None
        self._stop_event = threading.Event()
        self._flusher_thread: threading.Thread | None = None

        # 디렉터리 구성
        self._logs_dir = os.path.join(self.cfg.run_dir, "logs")
        os.makedirs(self._logs_dir, exist_ok=True)
        self._metrics_dir = os.path.join(self.cfg.run_dir, "metrics")
        os.makedirs(self._metrics_dir, exist_ok=True)
        self._figures_dir = os.path.join(self.cfg.run_dir, "figures")
        os.makedirs(self._figures_dir, exist_ok=True)
        self._configs_dir = os.path.join(self.cfg.run_dir, "configs")
        os.makedirs(self._configs_dir, exist_ok=True)

    # --------------- MQTT 수신 경로 ---------------

    def _on_connect(self, client: mqtt.Client, userdata, flags, rc, properties=None):
        if rc != 0:
            print(f"[collector] MQTT connect failed: rc={rc}")
            return
        client.subscribe("edge/+/+/event", qos=1)
        client.subscribe("policy/+/decision", qos=1)
        print(f"[collector] connected to mqtt://{self.cfg.broker}:{self.cfg.port}, subscribed topics.")

    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
        t_recv_ns = time.time_ns()
        self._last_ns = t_recv_ns

        topic = msg.topic
        payload = msg.payload
        qos = msg.qos
        dup = msg.dup
        retain = msg.retain
        try:
            if topic.startswith("edge/") and topic.endswith("/event"):
                self._handle_event_message(topic, payload, qos, dup, retain, t_recv_ns=t_recv_ns)
            elif topic.startswith("policy/") and topic.endswith("/decision"):
                self._handle_decision_message(topic, payload, t_recv_ns=t_recv_ns)
            else:
                # 기타 토픽은 무시(명세 외)
                return
        except Exception as e:
            # 수집기는 손실보다 지속성이 중요 — 개별 메시지 실패는 기록만 하고 계속 진행
            print(f"[collector] ERROR processing topic={topic}: {e}")

    # --------------- 이벤트 처리 ---------------

    def _handle_event_message(self, topic: str, payload_bytes: bytes,
                              qos: int, dup: bool, retain: bool,
                              t_recv_ns: int | None = None):
        """단위테스트에서 직접 호출 가능"""
        if t_recv_ns is None:
            t_recv_ns = time.time_ns()

        payload_len = len(payload_bytes)
        pkt_size = mqtt_v311_publish_size(topic, payload_len, qos=qos, dup=dup, retain=retain)

        data = json.loads(payload_bytes.decode("utf-8"))
        # 필수 필드 검증 (스키마: Event)
        # ts: int64 ns, seq: u64, device_id, sensor, val, pred, res, tau, kbits, profile, policy
        required = ("ts", "seq", "device_id", "sensor", "val", "pred", "res", "tau", "kbits", "profile", "policy")
        missing = [k for k in required if k not in data]
        if missing:
            raise ValueError(f"missing fields: {missing}")

        # 타입 캐스팅/정규화
        ts_ns = int(data["ts"])
        seq = int(data["seq"])
        device_id = str(data["device_id"])
        sensor = str(data["sensor"])
        val = float(data["val"])
        pred = float(data["pred"])
        res = float(data["res"])
        tau = float(data["tau"])
        kbits = int(data["kbits"])
        profile = str(data["profile"])
        policy = str(data["policy"])

        # 수신 즉시 AoI(ms) 계산(저장은 하지 않음; 로그/모니터링용)
        aoi_ms = (t_recv_ns - ts_ns - int(self.cfg.clock_offset_ns)) / 1e6

        key = (device_id, sensor, seq)

        with self._lock:
            self._bytes_total += pkt_size
            if key in self._events:
                # 중복: 최초 레코드의 mqtt_size_bytes에 누적만(데이터는 그대로)
                self._dup_messages += 1
                self._dup_bytes += pkt_size
                self._events[key]["mqtt_size_bytes"] += pkt_size
                # AoI는 최초 도착 기준 유지(엄격 재현성)
            else:
                self._events[key] = {
                    "device_id": device_id,
                    "sensor": sensor,
                    "profile": profile,
                    "policy": policy,
                    "seq": seq,
                    "ts_ns": ts_ns,
                    "t_recv_ns": int(t_recv_ns),
                    "val": val,
                    "pred": pred,
                    "res": res,
                    "tau": float(tau),
                    "kbits": int(kbits),
                    "topic": topic,
                    "mqtt_size_bytes": int(pkt_size),
                    "dup_flag": False,  # dedup 결과는 항상 False(중복 레코드는 저장하지 않음)
                }

        # 경량 실시간 로그(빈도 제한 없음; 외부 rate-limit 필요시 조정)
        print(f"[event] {device_id}/{sensor} seq={seq} aoi_ms={aoi_ms:.1f} bytes+={pkt_size}")

    # --------------- 정책결정 처리 ---------------

    def _handle_decision_message(self, topic: str, payload_bytes: bytes, t_recv_ns: int | None = None):
        if t_recv_ns is None:
            t_recv_ns = time.time_ns()
        data = json.loads(payload_bytes.decode("utf-8"))
        required = ("ts", "device_id", "state_aoi", "state_res", "state_res_var",
                    "state_loss", "state_q_len", "tau", "kbits", "reward")
        missing = [k for k in required if k not in data]
        if missing:
            raise ValueError(f"missing fields: {missing}")
        rec = {
            "ts": int(data["ts"]),
            "t_recv_ns": int(t_recv_ns),
            "device_id": str(data["device_id"]),
            "state_aoi": float(data["state_aoi"]),
            "state_res": float(data["state_res"]),
            "state_res_var": float(data["state_res_var"]),
            "state_loss": float(data["state_loss"]),
            "state_q_len": int(data["state_q_len"]),
            "tau": float(data["tau"]),
            "kbits": int(data["kbits"]),
            "reward": float(data["reward"]),
            "topic": topic,
        }
        with self._lock:
            self._decisions.append(rec)

    # --------------- 저장/플러시 ---------------

    def _flush(self):
        """events.parquet / decisions.parquet / collector_meta.json 저장(원자적 교체)"""
        with self._lock:
            events_list = list(self._events.values())
            decisions_list = list(self._decisions)
            bytes_total = int(self._bytes_total)
            dup_msgs = int(self._dup_messages)
            dup_bytes = int(self._dup_bytes)
            first_ns = int(self._first_ns)
            last_ns = int(self._last_ns)

        # DataFrame 변환 및 dtype 고정
        if events_list:
            df_e = pd.DataFrame.from_records(events_list)
            # dtype 지정(스키마 불변 강제)
            dtype_map = {
                "device_id": "string",
                "sensor": "string",
                "profile": "string",
                "policy": "string",
                "seq": "uint64",
                "ts_ns": "int64",
                "t_recv_ns": "int64",
                "val": "float64",
                "pred": "float64",
                "res": "float64",
                "tau": "float32",
                "kbits": "int16",
                "topic": "string",
                "mqtt_size_bytes": "int32",
                "dup_flag": "boolean",
            }
            for c, dt in dtype_map.items():
                if c in df_e.columns:
                    df_e[c] = df_e[c].astype(dt)
            tmp = os.path.join(self._logs_dir, "events.parquet.tmp")
            dst = os.path.join(self._logs_dir, "events.parquet")
            df_e.to_parquet(tmp, index=False)
            os.replace(tmp, dst)

        if decisions_list:
            df_d = pd.DataFrame.from_records(decisions_list)
            # 결정 로그는 비교적 유연 — 필수 컬럼 위주로 저장
            tmp = os.path.join(self._logs_dir, "decisions.parquet.tmp")
            dst = os.path.join(self._logs_dir, "decisions.parquet")
            df_d.to_parquet(tmp, index=False)
            os.replace(tmp, dst)

        # 메타 저장
        meta = {
            "run_id": os.path.basename(self.cfg.run_dir.rstrip("/")),
            "broker": f"{self.cfg.broker}:{self.cfg.port}",
            "clock_offset_ns": int(self.cfg.clock_offset_ns),
            "first_recv_ns": first_ns,
            "last_recv_ns": last_ns,
            "bytes_total_including_dups": bytes_total,
            "dup_messages_dropped": dup_msgs,
            "dup_bytes_dropped": dup_bytes,
            "events_unique": len(events_list),
            "decisions_count": len(decisions_list),
        }
        tmp = os.path.join(self._logs_dir, "collector_meta.json.tmp")
        dst = os.path.join(self._logs_dir, "collector_meta.json")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp, dst)

        print(f"[collector] flush: events={len(events_list)} decisions={len(decisions_list)} "
              f"bytes_total={bytes_total} dup_msgs={dup_msgs}")

    def start(self):
        self._client = mqtt.Client(client_id=self.cfg.client_id, clean_session=True, protocol=mqtt.MQTTv311)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        # subscriber side는 username/password 필요시 환경변수 등으로 확장
        self._client.connect(self.cfg.broker, self.cfg.port, keepalive=60)
        self._client.loop_start()

        # 주기적 플러시(원자적 저장)
        def _flusher():
            while not self._stop_event.wait(self.cfg.flush_interval_s):
                try:
                    self._flush()
                except Exception as e:
                    print(f"[collector] flush error: {e}")

        self._flusher_thread = threading.Thread(target=_flusher, daemon=True)
        self._flusher_thread.start()

        # 종료 시그널 처리
        def _handle_sig(signum, frame):
            print(f"[collector] signal={signum} received; stopping...")
            self.stop()

        signal.signal(signal.SIGINT, _handle_sig)
        signal.signal(signal.SIGTERM, _handle_sig)

        print("[collector] started. Press Ctrl+C to stop.")
        # 메인 스레드는 단순 대기
        try:
            while not self._stop_event.is_set():
                time.sleep(0.2)
        finally:
            # 외부 stop 호출 없이도 안전 정리
            self.stop()

    def stop(self):
        if self._stop_event.is_set():
            return
        self._stop_event.set()
        if self._client:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
        try:
            self._flush()
        except Exception as e:
            print(f"[collector] final flush error: {e}")
        if self._flusher_thread and self._flusher_thread.is_alive():
            self._flusher_thread.join(timeout=2.0)
        print("[collector] stopped.")


def main():
    parser = argparse.ArgumentParser(description="Semantic Uplink Collector (QoS1 de-dup, Parquet sink)")
    parser.add_argument("--run-dir", required=True, help="artifacts/{run_id} 경로(사전 생성 권장)")
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--flush-interval-s", type=int, default=10)
    parser.add_argument("--client-id", default="collector")
    parser.add_argument("--clock-offset-ns", type=int, default=0)
    args = parser.parse_args()

    cfg = Config(run_dir=args.run_dir, broker=args.broker, port=args.port,
                 flush_interval_s=args.flush_interval_s, client_id=args.client_id,
                 clock_offset_ns=args.clock_offset_ns)
    os.makedirs(cfg.run_dir, exist_ok=True)
    Collector(cfg).start()


if __name__ == "__main__":
    main()
