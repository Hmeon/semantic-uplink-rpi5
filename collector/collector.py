# collector/collector.py
# Python 3.10+
# 외부 의존: paho-mqtt, pandas, pyarrow
# 내부 의존: common.mqttutil.mqtt_v311_publish_size (이전 단계에 합의된 함수)

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import paho.mqtt.client as mqtt
import pandas as pd

from common.jsonutil import loads as _json_loads
from common.logging_setup import add_logging_cli_args, setup_logging_from_args

logger = logging.getLogger(__name__)

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
    max_runtime_s: float | None = None  # test helper: stop after N seconds
    dedup_cache_max_keys: int = 100_000
    dedup_cache_ttl_s: float = 300.0
    clock_offset_ns: int = 0   # (옵션) edge→collector 보정치. 검증 단계에서는 0. 


class Collector:
    """
    QoS1 중복 제거(seq 기반), AoI 계산(실시간 로깅용), Parquet 저장.
    스키마/폴더 규칙은 VALIDATION.md 고정안과 동일.
    """
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = threading.Lock()
        self._flush_lock = threading.Lock()

        # dedup된 이벤트를 key→row(dict)로 축적
        self._pending_events: dict[tuple[str, str, int], dict[str, Any]] = {}
        # policy decisions는 전부 저장(중복 처리 불필요)
        self._pending_decisions: list[dict[str, Any]] = []
        # 마커(버튼/실험) 로그
        self._pending_markers: list[dict[str, Any]] = []

        # bounded de-dup cache across flushes: (device_id, sensor, seq) -> last_seen_ns
        self._seen_event_keys: OrderedDict[tuple[str, str, int], int] = OrderedDict()

        # totals for meta/reporting (monotonic)
        self._events_unique_total = 0
        self._decisions_total = 0
        self._markers_total = 0

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

        # parquet rotation indices
        self._events_part = self._next_part_index("events")
        self._decisions_part = self._next_part_index("decisions")
        self._markers_part = self._next_part_index("markers")
        self._storage_format = "parquet"

    def _next_part_index(self, prefix: str) -> int:
        pat = re.compile(rf"^{re.escape(prefix)}_(\d+)\.(parquet|csv)$")
        max_idx = 0
        try:
            for name in os.listdir(self._logs_dir):
                m = pat.match(name)
                if m:
                    max_idx = max(max_idx, int(m.group(1)))
        except FileNotFoundError:
            return 1
        return max_idx + 1

    def _prune_seen(self, now_ns: int) -> None:
        ttl_ns = int(max(0.0, float(self.cfg.dedup_cache_ttl_s)) * 1e9)
        if ttl_ns > 0 and self._seen_event_keys:
            cutoff = int(now_ns) - ttl_ns
            while self._seen_event_keys:
                _key, last_seen_ns = next(iter(self._seen_event_keys.items()))
                if int(last_seen_ns) >= cutoff:
                    break
                self._seen_event_keys.popitem(last=False)

        max_keys = int(self.cfg.dedup_cache_max_keys)
        if max_keys > 0:
            while len(self._seen_event_keys) > max_keys:
                self._seen_event_keys.popitem(last=False)

    # --------------- MQTT 수신 경로 --------------- 

    def _on_connect(self, client: mqtt.Client, userdata, flags, rc, properties=None):
        if rc != 0:
            logger.error("MQTT connect failed: rc=%s", rc)
            return
        client.subscribe("edge/+/+/event", qos=1)
        client.subscribe("policy/+/decision", qos=1)
        client.subscribe("marker/+", qos=1)
        logger.info(
            "connected to mqtt://%s:%s; subscribed topics",
            self.cfg.broker,
            self.cfg.port,
        )

    def _on_disconnect(self, client: mqtt.Client, userdata, rc, properties=None):
        # Broker outage / network flap is a normal scenario; keep the process alive.
        logger.warning("disconnected rc=%s", rc)

    def _on_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage):
        t_recv_ns = time.time_ns()
        self._last_ns = t_recv_ns

        topic = msg.topic
        payload = msg.payload
        qos = msg.qos
        dup = msg.dup
        retain = msg.retain
        try:
            self.ingest_message(
                topic=topic,
                payload=payload,
                qos=qos,
                dup=dup,
                retain=retain,
                t_recv_ns=t_recv_ns,
            )
        except Exception:
            # 수집기는 손실보다 지속성이 중요 — 개별 메시지 실패는 기록만 하고 계속 진행
            device_id = None
            sensor = None
            try:
                parts = str(topic).split("/")
                if len(parts) >= 2:
                    device_id = parts[1]
                if len(parts) >= 3:
                    sensor = parts[2]
            except Exception:
                device_id = None
                sensor = None

            seq = None
            try:
                d = _json_loads(payload)
                if isinstance(d, dict) and "seq" in d:
                    seq = d.get("seq")
            except Exception:
                seq = None

            logger.exception(
                "error processing message: topic=%s device_id=%s sensor=%s seq=%s payload_bytes=%s",
                topic,
                device_id,
                sensor,
                seq,
                len(payload) if payload is not None else None,
            )

    def ingest_message(
        self,
        *,
        topic: str,
        payload: bytes,
        qos: int = 1,
        dup: bool = False,
        retain: bool = False,
        t_recv_ns: int | None = None,
    ) -> None:
        """
        MQTT 메시지를 Collector에 주입한다.

        - 테스트/리플레이/대체 전송(예: 파일/HTTP)에서 브로커 없이도 동일한 처리 경로를 재사용한다.
        - 오류는 호출자에게 예외로 전파(런타임 MQTT 콜백에서는 try/except로 감싸 로그만 남김).
        """
        if t_recv_ns is None:
            t_recv_ns = time.time_ns()
        self._last_ns = int(t_recv_ns)

        if topic.startswith("edge/") and topic.endswith("/event"):
            self._handle_event_message(
                topic, payload, qos, dup, retain, t_recv_ns=int(t_recv_ns)
            )
        elif topic.startswith("policy/") and topic.endswith("/decision"):
            self._handle_decision_message(topic, payload, t_recv_ns=int(t_recv_ns))
        elif topic.startswith("marker/"):
            self._handle_marker_message(topic, payload, t_recv_ns=int(t_recv_ns))
        else:
            return

    def flush_once(self) -> None:
        """현재 pending 버퍼를 1회 flush한다(테스트/운영 트러블슈팅 용)."""
        self._flush()

    # --------------- 이벤트 처리 --------------- 

    def _handle_event_message(self, topic: str, payload_bytes: bytes,
                              qos: int, dup: bool, retain: bool,
                              t_recv_ns: int | None = None):
        """단위테스트에서 직접 호출 가능"""
        if t_recv_ns is None:
            t_recv_ns = time.time_ns()

        payload_len = len(payload_bytes)
        pkt_size = mqtt_v311_publish_size(topic, payload_len, qos=qos, dup=dup, retain=retain)

        data = _json_loads(payload_bytes)
        if not isinstance(data, dict):
            raise ValueError("invalid JSON: expected an object")
        # 필수 필드 검증 (스키마: Event)
        # ts: int64 ns, seq: u64, device_id, sensor, val, pred, res, tau, kbits, profile, policy
        required = (
            "ts",
            "seq",
            "device_id",
            "sensor",
            "val",
            "pred",
            "res",
            "tau",
            "kbits",
            "profile",
            "policy",
        )
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
        event_reason = None
        if "event_reason" in data and data["event_reason"] is not None:
            event_reason = str(data["event_reason"])

        # 수신 즉시 AoI(ms) 계산(저장은 하지 않음; 로그/모니터링용)
        aoi_ms = (t_recv_ns - ts_ns - int(self.cfg.clock_offset_ns)) / 1e6

        key = (device_id, sensor, seq)

        is_dup = False
        with self._lock:
            self._prune_seen(int(t_recv_ns))
            self._bytes_total += pkt_size
            if key in self._pending_events:
                # duplicate within current pending batch: keep first row but accumulate bytes
                is_dup = True
                self._dup_messages += 1
                self._dup_bytes += pkt_size
                self._pending_events[key]["mqtt_size_bytes"] += pkt_size
            elif key in self._seen_event_keys:
                # duplicate across flush boundaries: count/drop without rewriting old parquet parts
                is_dup = True
                self._dup_messages += 1
                self._dup_bytes += pkt_size
            else:
                self._pending_events[key] = {
                    "device_id": device_id,
                    "sensor": sensor,
                    "profile": profile,
                    "policy": policy,
                    "event_reason": event_reason,
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
                self._events_unique_total += 1

            # bounded de-dup cache across flushes (LRU-ish by last seen time)
            if int(self.cfg.dedup_cache_max_keys) > 0 or float(self.cfg.dedup_cache_ttl_s) > 0.0:
                self._seen_event_keys.pop(key, None)
                self._seen_event_keys[key] = int(t_recv_ns)
                self._prune_seen(int(t_recv_ns))

        # 경량 실시간 로그(빈도 제한 없음; 외부 rate-limit 필요시 조정)
        tag = "DUP" if is_dup else "OK"
        logger.debug(
            "event=%s device_id=%s sensor=%s seq=%s aoi_ms=%.1f bytes=%s",
            tag,
            device_id,
            sensor,
            seq,
            float(aoi_ms),
            pkt_size,
        )

    # --------------- 정책결정 처리 --------------- 

    def _handle_decision_message(
        self, topic: str, payload_bytes: bytes, t_recv_ns: int | None = None
    ):
        if t_recv_ns is None:
            t_recv_ns = time.time_ns()
        data = _json_loads(payload_bytes)
        if not isinstance(data, dict):
            raise ValueError("invalid JSON: expected an object")
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
        for k in [
            "arm_id",
            "safe_arm_forced",
            "forced_reason",
            "ucb_exploitation",
            "ucb_exploration",
            "ucb_score",
            "ucb_alpha",
            "reward_aoi",
            "reward_mae",
            "reward_rate",
            "rate_limit_skips",
        ]:
            if k in data and data[k] is not None:
                rec[k] = data[k]
        with self._lock:
            self._pending_decisions.append(rec)
            self._decisions_total += 1

    def _handle_marker_message(
        self, topic: str, payload_bytes: bytes, t_recv_ns: int | None = None
    ):
        if t_recv_ns is None:
            t_recv_ns = time.time_ns()
        data = _json_loads(payload_bytes)
        if not isinstance(data, dict):
            raise ValueError("invalid JSON: expected an object")
        rec = {
            "ts": int(data.get("ts", t_recv_ns)),
            "t_recv_ns": int(t_recv_ns),
            "device_id": str(data.get("device_id", "unknown")),
            "note": str(data.get("note", "")),
            "topic": topic,
        }
        with self._lock:
            self._pending_markers.append(rec)
            self._markers_total += 1
        logger.info(
            "marker device_id=%s note=%s ts=%s",
            rec.get("device_id"),
            rec.get("note"),
            rec.get("ts"),
        )

    # --------------- 저장/플러시 --------------- 

    def _flush(self):
        """Write pending buffers to rotated Parquet parts and update collector_meta.json."""
        with self._flush_lock:
            with self._lock:
                pending_events = self._pending_events
                pending_decisions = self._pending_decisions
                pending_markers = self._pending_markers

                # swap buffers so we can flush without blocking the MQTT callback path on disk I/O
                self._pending_events = {}
                self._pending_decisions = []
                self._pending_markers = []

                bytes_total = int(self._bytes_total)
                dup_msgs = int(self._dup_messages)
                dup_bytes = int(self._dup_bytes)
                first_ns = int(self._first_ns)
                last_ns = int(self._last_ns)
                events_unique_total = int(self._events_unique_total)
                decisions_total = int(self._decisions_total)
                markers_total = int(self._markers_total)

                events_part: int | None = None
                decisions_part: int | None = None
                markers_part: int | None = None
                if pending_events:
                    events_part = int(self._events_part)
                    self._events_part += 1
                if pending_decisions:
                    decisions_part = int(self._decisions_part)
                    self._decisions_part += 1
                if pending_markers:
                    markers_part = int(self._markers_part)
                    self._markers_part += 1

            def _write_csv(df: pd.DataFrame, fname: str) -> None:
                tmp = os.path.join(self._logs_dir, f"{fname}.tmp")
                dst = os.path.join(self._logs_dir, fname)
                df.to_csv(tmp, index=False)
                os.replace(tmp, dst)

            def _write_table(df: pd.DataFrame, base: str) -> None:
                if self._storage_format == "csv":
                    _write_csv(df, f"{base}.csv")
                    return
                tmp = os.path.join(self._logs_dir, f"{base}.parquet.tmp")
                dst = os.path.join(self._logs_dir, f"{base}.parquet")
                try:
                    df.to_parquet(tmp, index=False)
                    os.replace(tmp, dst)
                except ImportError as exc:
                    if os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
                    self._storage_format = "csv"
                    logger.warning(
                        "parquet_unavailable_fallback_to_csv error=%s",
                        exc,
                    )
                    _write_csv(df, f"{base}.csv")
                except ValueError as exc:
                    msg = str(exc)
                    if "usable engine" in msg or "parquet" in msg:
                        if os.path.exists(tmp):
                            try:
                                os.remove(tmp)
                            except OSError:
                                pass
                        self._storage_format = "csv"
                        logger.warning(
                            "parquet_unavailable_fallback_to_csv error=%s",
                            exc,
                        )
                        _write_csv(df, f"{base}.csv")
                    else:
                        raise

            events_written = 0
            decisions_written = 0
            markers_written = 0
            events_ok = False
            decisions_ok = False
            markers_ok = False

            try:
                if pending_events and events_part is not None:
                    df_e = pd.DataFrame.from_records(list(pending_events.values()))
                    dtype_map = {
                        "device_id": "string",
                        "sensor": "string",
                        "profile": "string",
                        "policy": "string",
                        "event_reason": "string",
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
                    _write_table(df_e, f"events_{events_part:06d}")
                    events_written = len(df_e)
                    events_ok = True

                if pending_decisions and decisions_part is not None:
                    df_d = pd.DataFrame.from_records(list(pending_decisions))
                    dtype_map = {
                        "ts": "int64",
                        "t_recv_ns": "int64",
                        "device_id": "string",
                        "state_aoi": "float64",
                        "state_res": "float64",
                        "state_res_var": "float64",
                        "state_loss": "float64",
                        "state_q_len": "int64",
                        "tau": "float32",
                        "kbits": "int16",
                        "reward": "float64",
                        "topic": "string",
                        # optional diagnostics
                        "arm_id": "Int64",
                        "safe_arm_forced": "boolean",
                        "forced_reason": "string",
                        "ucb_exploitation": "float64",
                        "ucb_exploration": "float64",
                        "ucb_score": "float64",
                        "ucb_alpha": "float64",
                        "reward_aoi": "float64",
                        "reward_mae": "float64",
                        "reward_rate": "float64",
                        "rate_limit_skips": "Int64",
                    }
                    for c, dt in dtype_map.items():
                        if c in df_d.columns:
                            df_d[c] = df_d[c].astype(dt)
                    _write_table(df_d, f"decisions_{decisions_part:06d}")
                    decisions_written = len(df_d)
                    decisions_ok = True

                if pending_markers and markers_part is not None:
                    df_m = pd.DataFrame.from_records(list(pending_markers))
                    _write_table(df_m, f"markers_{markers_part:06d}")
                    markers_written = len(df_m)
                    markers_ok = True
            except Exception:
                with self._lock:
                    if pending_events and not events_ok:
                        for k, v in pending_events.items():
                            self._pending_events.setdefault(k, v)
                    if pending_decisions and not decisions_ok:
                        self._pending_decisions = list(pending_decisions) + self._pending_decisions
                    if pending_markers and not markers_ok:
                        self._pending_markers = list(pending_markers) + self._pending_markers
                raise

            meta = {
                "run_id": os.path.basename(self.cfg.run_dir.rstrip("/")),
                "broker": f"{self.cfg.broker}:{self.cfg.port}",
                "clock_offset_ns": int(self.cfg.clock_offset_ns),
                "first_recv_ns": first_ns,
                "last_recv_ns": last_ns,
                "bytes_total_including_dups": bytes_total,
                "dup_messages_dropped": dup_msgs,
                "dup_bytes_dropped": dup_bytes,
                "events_unique": events_unique_total,
                "decisions_count": decisions_total,
                "markers_count": markers_total,
            }
            tmp = os.path.join(self._logs_dir, "collector_meta.json.tmp")
            dst = os.path.join(self._logs_dir, "collector_meta.json")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            os.replace(tmp, dst)

            logger.info(
                "flush events+=%s decisions+=%s markers+=%s total_events_unique=%s "
                "bytes_total=%s dup_msgs=%s",
                events_written,
                decisions_written,
                markers_written,
                events_unique_total,
                bytes_total,
                dup_msgs,
            )

    def start(self):
        t0 = time.time()
        self._client = mqtt.Client(
            client_id=self.cfg.client_id, clean_session=True, protocol=mqtt.MQTTv311
        )
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect
        try:
            self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        except Exception:
            pass
        # subscriber side는 username/password 필요시 환경변수 등으로 확장
        # connect_async keeps the collector alive even when the broker is down on startup.
        self._client.connect_async(self.cfg.broker, self.cfg.port, keepalive=60)
        self._client.loop_start()

        # 주기적 플러시(원자적 저장)
        def _flusher():
            while not self._stop_event.wait(self.cfg.flush_interval_s):
                try:
                    self._flush()
                except Exception:
                    logger.exception("flush error")

        self._flusher_thread = threading.Thread(target=_flusher, daemon=True)
        self._flusher_thread.start()

        # 종료 시그널 처리
        def _handle_sig(signum, frame):
            logger.info("signal=%s received; stopping", signum)
            self.stop()

        signal.signal(signal.SIGINT, _handle_sig)
        signal.signal(signal.SIGTERM, _handle_sig)

        logger.info("started")
        # 메인 스레드는 단순 대기
        try:
            while not self._stop_event.is_set():
                if self.cfg.max_runtime_s is not None:
                    if (time.time() - t0) >= float(self.cfg.max_runtime_s):
                        logger.info("max_runtime_s=%s reached; stopping", self.cfg.max_runtime_s)
                        break
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
        except Exception:
            logger.exception("final flush error")
        if self._flusher_thread and self._flusher_thread.is_alive():
            self._flusher_thread.join(timeout=2.0)
        logger.info("stopped")


def main():
    parser = argparse.ArgumentParser(
        description="Semantic Uplink Collector (QoS1 de-dup, Parquet sink)"
    )
    parser.add_argument("--run-dir", required=True, help="artifacts/{run_id} 경로(사전 생성 권장)")
    parser.add_argument("--broker", default="localhost")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--flush-interval-s", type=int, default=10)
    parser.add_argument("--client-id", default="collector")
    parser.add_argument("--clock-offset-ns", type=int, default=0)
    parser.add_argument("--dedup-cache-max-keys", type=int, default=100_000)
    parser.add_argument("--dedup-cache-ttl-s", type=float, default=300.0)
    parser.add_argument(
        "--max-runtime-s",
        type=float,
        default=None,
        help="Stop automatically after N seconds (useful for tests).",
    )
    add_logging_cli_args(parser)
    args = parser.parse_args()
    setup_logging_from_args(args)

    cfg = Config(
        run_dir=args.run_dir,
        broker=args.broker,
        port=args.port,
        flush_interval_s=args.flush_interval_s,
        client_id=args.client_id,
        max_runtime_s=args.max_runtime_s,
        dedup_cache_max_keys=args.dedup_cache_max_keys,
        dedup_cache_ttl_s=args.dedup_cache_ttl_s,
        clock_offset_ns=args.clock_offset_ns,
    )
    os.makedirs(cfg.run_dir, exist_ok=True)
    Collector(cfg).start()


if __name__ == "__main__":
    main()
