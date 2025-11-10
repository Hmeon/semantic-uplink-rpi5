# edge/edge_daemon.py
# Python 3.10+
# 목적: 센서(mic_rms/temp) → EWMA(고정 τ) → EventMsg → Outbox(SQLite) → MQTTPublisher(QoS1)
#       엔드-투-엔드 파이프라인 오케스트레이션.
# - 재현성: ns 타임스탬프/seq 보존, (device_id,sensor,seq) 기준 중복 제거는 collector에서 수행.
# - 최소 복잡도: 정책은 고정 τ(ETS) 기준선에 집중. LinUCB는 별도 단계에서 결합.
# - 유실 0: Outbox 내구성 + 퍼블리셔 재연결/ACK 기반 삭제로 보장.
# - 문서/토픽/스키마: 동결안 및 공통 모듈과 1:1 정합.

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

from common.schema import SensorType, LinkProfile
from edge.sensors.mic_rms import MicRMS
from edge.sensors.temp import TempSensor
from edge.predict.ewma import EWMAConfig, EWMAPredictor
from edge.uploader.outbox import Outbox
from edge.uploader.mqtt_publisher import MQTTPublisher
from edge.rtc import DS3231, RTCGuardian


@dataclass(slots=True)
class MicCfg:
    enable: bool = True
    backend: str = "auto"              # auto|sounddevice|arecord
    arecord_device: str | None = None
    sounddevice_device: str | None = None
    sample_rate: int = 16_000
    frame_ms: int = 100
    alpha: float = 0.2                 # EWMA
    tau: float = 3.0                   # dB
    kbits: int = 6
    heartbeat_s: float | None = 10.0
    min_emit_ms: int = 0               # 폭주 방지 0=비활성


@dataclass(slots=True)
class TempCfg:
    enable: bool = True
    backend: str = "auto"              # auto|w1|sysfs|mock
    sample_hz: float = 1.0
    alpha: float = 0.5
    tau: float = 0.2                   # ℃
    kbits: int = 8
    heartbeat_s: float | None = 10.0
    min_emit_ms: int = 0
    w1_path: str | None = None
    sysfs_path: str | None = None


@dataclass(slots=True)
class RTCCfg:
    enable: bool = False
    bus: int = 1
    address: int = 0x68
    drift_guard_s: float = 2.0
    resync_interval_s: float = 900.0
    push_system_to_rtc: bool = False


class EdgeDaemon:
    def __init__(
        self,
        *,
        device_id: str,
        profile: LinkProfile,
        outbox_path: str,
        broker: str = "localhost",
        port: int = 1883,
        client_id: str = "edge-pub",
        keepalive: int = 30,
        mic: MicCfg | None = None,
        temp: TempCfg | None = None,
        rtc: RTCCfg | None = None,
    ):
        self.device_id = device_id
        self.profile = profile
        self.outbox_path = outbox_path
        self.broker = broker
        self.port = int(port)
        self.client_id = client_id
        self.keepalive = int(keepalive)
        self.mic_cfg = mic or MicCfg()
        self.temp_cfg = temp or TempCfg()
        self.rtc_cfg = rtc or RTCCfg()

        # 런타임
        os.makedirs(os.path.dirname(self.outbox_path) or ".", exist_ok=True)
        self.outbox = Outbox(self.outbox_path)
        self.publisher = MQTTPublisher(
            self.outbox,
            broker=self.broker,
            port=self.port,
            client_id=self.client_id,
            keepalive=self.keepalive,
        )

        self._stop = threading.Event()
        self._mic_thread: Optional[threading.Thread] = None
        self._temp_thread: Optional[threading.Thread] = None
        self._rtc_device: DS3231 | None = None
        self._rtc_guardian: RTCGuardian | None = None

    # ---------- 라이프사이클 ----------

    def start(self):
        self._install_signals()
        self._maybe_start_rtc()
        self.publisher.start()

        if self.mic_cfg.enable:
            self._mic_thread = threading.Thread(target=self._mic_loop, name="edge-mic", daemon=True)
            self._mic_thread.start()

        if self.temp_cfg.enable:
            self._temp_thread = threading.Thread(target=self._temp_loop, name="edge-temp", daemon=True)
            self._temp_thread.start()

        print("[edge] started. Press Ctrl+C to stop.")
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        finally:
            self.stop()

    def stop(self):
        if self._stop.is_set():
            return
        self._stop.set()
        # 센서 루프가 read()에서 블록 중일 수 있으니 close()로 깨운다.
        if getattr(self, "_mic_obj", None):
            try:
                self._mic_obj.close()
            except Exception:
                pass
        if getattr(self, "_temp_obj", None):
            try:
                self._temp_obj.close()
            except Exception:
                pass
        if self._mic_thread and self._mic_thread.is_alive():
            self._mic_thread.join(timeout=2.0)
        if self._temp_thread and self._temp_thread.is_alive():
            self._temp_thread.join(timeout=2.0)
        try:
            self.publisher.stop()
        finally:
            self.outbox.close()
        self._stop_rtc()
        print("[edge] stopped.")

    # ---------- 내부: 센서 루프 ----------

    def _maybe_start_rtc(self):
        if not self.rtc_cfg.enable or self._rtc_guardian is not None:
            return
        try:
            self._rtc_device = DS3231(bus=self.rtc_cfg.bus, address=self.rtc_cfg.address)
            self._rtc_guardian = RTCGuardian(
                self._rtc_device,
                drift_guard_s=self.rtc_cfg.drift_guard_s,
                resync_interval_s=self.rtc_cfg.resync_interval_s,
                push_system_to_rtc=self.rtc_cfg.push_system_to_rtc,
            )
            status = self._rtc_guardian.guard_once()
            if status.rtc_time is None:
                print(f"[edge] RTC unavailable: {status.last_error}")
                self._rtc_guardian = None
                if self._rtc_device is not None:
                    try:
                        self._rtc_device.close()
                    except Exception:
                        pass
                    self._rtc_device = None
                return
            drift = status.drift_seconds or 0.0
            print(f"[edge] RTC sync: rtc={status.rtc_time.isoformat()} drift={drift:.3f}s")
            if self.rtc_cfg.resync_interval_s > 0:
                self._rtc_guardian.start()
        except Exception as e:
            print(f"[edge] RTC init failed: {e}")
            self._rtc_guardian = None
            if self._rtc_device is not None:
                try:
                    self._rtc_device.close()
                except Exception:
                    pass
                self._rtc_device = None

    def _stop_rtc(self):
        if self._rtc_guardian is not None:
            try:
                self._rtc_guardian.stop()
            except Exception:
                pass
            self._rtc_guardian = None
        if self._rtc_device is not None:
            try:
                self._rtc_device.close()
            except Exception:
                pass
            self._rtc_device = None

    def _mic_loop(self):
        cfg = self.mic_cfg
        # 센서 준비
        self._mic_obj = MicRMS(
            device_id=self.device_id,
            sample_rate=cfg.sample_rate,
            frame_ms=cfg.frame_ms,
            backend=cfg.backend,
            arecord_device=cfg.arecord_device,
            sounddevice_device=cfg.sounddevice_device,
        )
        pred = EWMAPredictor(EWMAConfig(
            device_id=self.device_id,
            sensor=SensorType.MIC_RMS,
            alpha=cfg.alpha,
            tau=cfg.tau,
            kbits=cfg.kbits,
            profile=self.profile,
            heartbeat_s=cfg.heartbeat_s,
            min_emit_interval_ms=cfg.min_emit_ms,
            bootstrap_emit=True,
        ))
        print(f"[edge] mic loop started: {self._mic_obj!r} α={cfg.alpha} τ={cfg.tau} k={cfg.kbits}")
        try:
            for s in self._mic_obj.stream(duration_s=None):
                if self._stop.is_set():
                    break
                evt = pred.predict_and_maybe_emit(s)
                if evt is None:
                    continue
                # Outbox 적재(QoS1), created_ns=evt.ts → AoI/Rate 재현성
                try:
                    self.outbox.enqueue(evt.mqtt_topic(), evt.to_json_bytes(), qos=1, retain=False, created_ns=evt.ts)
                except Exception as e:
                    print(f"[edge] outbox enqueue error(mic): {e}")
        except SystemExit:
            pass
        except Exception as e:
            print(f"[edge] mic loop error: {e}")
        finally:
            try:
                self._mic_obj.close()
            except Exception:
                pass
            print("[edge] mic loop stopped")

    def _temp_loop(self):
        cfg = self.temp_cfg
        # 센서 준비
        self._temp_obj = TempSensor(
            device_id=self.device_id,
            backend=cfg.backend,
            sample_hz=cfg.sample_hz,
            w1_path=cfg.w1_path,
            sysfs_path=cfg.sysfs_path,
        )
        pred = EWMAPredictor(EWMAConfig(
            device_id=self.device_id,
            sensor=SensorType.TEMP,
            alpha=cfg.alpha,
            tau=cfg.tau,
            kbits=cfg.kbits,
            profile=self.profile,
            heartbeat_s=cfg.heartbeat_s,
            min_emit_interval_ms=cfg.min_emit_ms,
            bootstrap_emit=True,
        ))
        print(f"[edge] temp loop started: {self._temp_obj!r} α={cfg.alpha} τ={cfg.tau} k={cfg.kbits}")
        try:
            for s in self._temp_obj.stream(duration_s=None):
                if self._stop.is_set():
                    break
                evt = pred.predict_and_maybe_emit(s)
                if evt is None:
                    continue
                try:
                    self.outbox.enqueue(evt.mqtt_topic(), evt.to_json_bytes(), qos=1, retain=False, created_ns=evt.ts)
                except Exception as e:
                    print(f"[edge] outbox enqueue error(temp): {e}")
        except SystemExit:
            pass
        except Exception as e:
            print(f"[edge] temp loop error: {e}")
        finally:
            try:
                self._temp_obj.close()
            except Exception:
                pass
            print("[edge] temp loop stopped")

    # ---------- 기타 ----------

    def _install_signals(self):
        def _h(signum, frame):
            print(f"[edge] signal={signum} -> stopping...")
            self.stop()
            sys.exit(0)
        signal.signal(signal.SIGINT, _h)
        signal.signal(signal.SIGTERM, _h)


# ---------------- CLI ----------------

def _mk_run_dirs(run_dir: str) -> str:
    os.makedirs(run_dir, exist_ok=True)
    return run_dir

def _default_run_dir(device_id: str) -> str:
    # artifacts/<UTC-ish timestamp>_<device_id>
    ts = time.strftime("%Y-%m-%dT%H-%M-%SZ", time.gmtime())
    return os.path.join("artifacts", f"{ts}_{device_id}")

def main():
    p = argparse.ArgumentParser(description="Edge daemon: sensors → EWMA(τ) → Outbox → MQTT QoS1")
    # 공통
    p.add_argument("--device-id", required=True)
    p.add_argument("--profile", choices=[e.value for e in LinkProfile], default=LinkProfile.SLOW_10KBPS.value)
    p.add_argument("--run-dir", default=None, help="기록 루트(artifacts/<ts>_<device_id> 기본)")
    p.add_argument("--broker", default="localhost")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--client-id", default="edge-pub")
    p.add_argument("--keepalive", type=int, default=30)

    # Outbox
    p.add_argument("--outbox", default=None, help="SQLite 경로(기본: <run-dir>/outbox.sqlite)")

    # RTC 옵션
    p.add_argument("--rtc-enable", action="store_true", default=False, help="DS3231 RTC 가드 활성화")
    p.add_argument("--rtc-bus", type=int, default=1, help="DS3231 I2C bus 번호")
    p.add_argument("--rtc-address", type=_parse_int_auto, default=0x68, help="DS3231 I2C 주소(예: 0x68)")
    p.add_argument("--rtc-drift-guard", type=float, default=2.0, help="RTC와 시스템간 허용 오차(초)")
    p.add_argument("--rtc-resync", type=float, default=900.0, help="재동기화 주기(초), <=0이면 1회만")
    p.add_argument("--rtc-push-system", action="store_true", help="시스템 시간이 앞설 때 RTC에 기록")

    # MIC 옵션
    p.add_argument("--mic-enable", action="store_true", default=False, help="마이크 스트림 활성화")
    p.add_argument("--mic-backend", choices=["auto", "sounddevice", "arecord"], default="auto")
    p.add_argument("--mic-arecord-device", default=None)
    p.add_argument("--mic-sd-device", default=None)
    p.add_argument("--mic-sr", type=int, default=16000)
    p.add_argument("--mic-frame-ms", type=int, default=100)
    p.add_argument("--mic-alpha", type=float, default=0.2)
    p.add_argument("--mic-tau", type=float, default=3.0)
    p.add_argument("--mic-kbits", type=int, default=6)
    p.add_argument("--mic-heartbeat", type=float, default=10.0)
    p.add_argument("--mic-min-emit-ms", type=int, default=0)

    # TEMP 옵션
    p.add_argument("--temp-enable", action="store_true", default=False, help="온도 스트림 활성화")
    p.add_argument("--temp-backend", choices=["auto", "w1", "sysfs", "mock"], default="auto")
    p.add_argument("--temp-hz", type=float, default=1.0)
    p.add_argument("--temp-alpha", type=float, default=0.5)
    p.add_argument("--temp-tau", type=float, default=0.2)
    p.add_argument("--temp-kbits", type=int, default=8)
    p.add_argument("--temp-heartbeat", type=float, default=10.0)
    p.add_argument("--temp-min-emit-ms", type=int, default=0)
    p.add_argument("--temp-w1-path", default=None)
    p.add_argument("--temp-sysfs-path", default=None)

    args = p.parse_args()

    # 기본 run-dir/outbox
    run_dir = args.run_dir or _default_run_dir(args.device_id)
    _mk_run_dirs(run_dir)
    outbox_path = args.outbox or os.path.join(run_dir, "outbox.sqlite")

    mic_cfg = MicCfg(
        enable=bool(args.mic_enable),
        backend=args.mic_backend,
        arecord_device=args.mic_arecord_device,
        sounddevice_device=args.mic_sd_device,
        sample_rate=args.mic_sr,
        frame_ms=args.mic_frame_ms,
        alpha=args.mic_alpha,
        tau=args.mic_tau,
        kbits=args.mic_kbits,
        heartbeat_s=_hb_none(args.mic_heartbeat),
        min_emit_ms=args.mic_min_emit_ms,
    )

    temp_cfg = TempCfg(
        enable=bool(args.temp_enable),
        backend=args.temp_backend,
        sample_hz=args.temp_hz,
        alpha=args.temp_alpha,
        tau=args.temp_tau,
        kbits=args.temp_kbits,
        heartbeat_s=(None if args.temp_heartbeat <= 0 else float(args.temp_heartbeat)),
        min_emit_ms=args.temp_min_emit_ms,
        w1_path=args.temp_w1_path,
        sysfs_path=args.temp_sysfs_path,
    )

    if not mic_cfg.enable and not temp_cfg.enable:
        print("[edge] ERROR: at least one sensor must be enabled (--mic-enable / --temp-enable)")
        sys.exit(2)

    rtc_cfg = RTCCfg(
        enable=bool(args.rtc_enable),
        bus=args.rtc_bus,
        address=args.rtc_address,
        drift_guard_s=args.rtc_drift_guard,
        resync_interval_s=args.rtc_resync,
        push_system_to_rtc=bool(args.rtc_push_system),
    )

    daemon = EdgeDaemon(
        device_id=args.device_id,
        profile=LinkProfile(args.profile),
        outbox_path=outbox_path,
        broker=args.broker,
        port=args.port,
        client_id=args.client_id,
        keepalive=args.keepalive,
        mic=mic_cfg,
        temp=temp_cfg,
        rtc=rtc_cfg,
    )
    try:
        daemon.start()
    finally:
        daemon.stop()


# 작은 실수 방지: heartbeat 0/음수 → 비활성(None) 변환용 헬퍼 (가독성 위해 분리)
def _hb_none(x: float | None) -> float | None:
    if x is None:
        return None
    try:
        xv = float(x)
    except Exception:
        return None
    return None if xv <= 0 else xv


def _parse_int_auto(val: str) -> int:
    try:
        return int(str(val), 0)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid integer literal: {val}") from e


if __name__ == "__main__":
    main()
