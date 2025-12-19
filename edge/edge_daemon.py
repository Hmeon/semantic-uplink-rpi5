# edge/edge_daemon.py
# Python 3.10+
# 목적: 센서(mic_rms/temp) → 예측(EWMA) → 정책(periodic/fixed/linucb) → EventMsg
#       → Outbox(SQLite) → MQTTPublisher(QoS1) : 엔드-투-엔드 파이프라인 오케스트레이션.
# - 재현성: ns 타임스탬프/seq 보존, (device_id,sensor,seq) 기준 중복 제거는 collector에서 수행.
# - 최소 복잡도: 정책은 고정 τ(ETS) 기준선에 집중. LinUCB는 별도 단계에서 결합.
# - 유실 0: Outbox 내구성 + 퍼블리셔 재연결/ACK 기반 삭제로 보장.
# - 문서/토픽/스키마: 동결안 및 공통 모듈과 1:1 정합.

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import yaml

from common.config import load_device_config, load_policy_config_dict
from common.jsonutil import dumps as _json_dumps
from common.logging_setup import add_logging_cli_args, setup_logging_from_args
from common.schema import LinkProfile, PolicyMode, SensorType
from edge.sensors.mic_rms import MicRMS
from edge.sensors.temp import TempSensor
from edge.predict.ewma import EWMAConfig
from edge.uploader.outbox import Outbox
from edge.uploader.mqtt_publisher import MQTTPublisher
from edge.rtc import DS3231, RTCGuardian
from edge.ui.lcd import DisplayConfig, build_display
from edge.ui.status import StatusTracker
from edge.policy.runtime import SensorPolicyRuntime, StepResult, load_linucb_config
from edge.ui.buttons import ButtonsConfig, build_buttons
from link.shaper import tc_profiles

logger = logging.getLogger(__name__)


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


@dataclass(slots=True)
class UICfg:
    enable: bool = False
    kind: str = "auto"         # auto|lcd1602|ssd1306|console
    bus: int = 1
    address: int | None = None
    refresh_s: float = 1.0
    rate_window_s: float = 10.0


@dataclass(slots=True)
class ButtonsCfg:
    enable: bool = True
    mode_pin: int = 17
    profile_pin: int = 27
    marker_pin: int = 22
    debounce_ms: int = 200


@dataclass(slots=True)
class LinkCfg:
    iface: str = "eth0"
    both: bool = False            # ingress 포함 여부
    apply_on_button: bool = False # 버튼으로 프로파일 변경 시 tc 적용 여부 (root 필요)
    apply_on_start: bool = False  # 시작 시 현재 profile을 1회 tc 적용 (권한 필요)
    profiles_config: str | None = None  # YAML로 tc profile override (옵션)


class EdgeDaemon:
    def __init__(
        self,
        *,
        device_id: str,
        profile: LinkProfile,
        outbox_path: str,
        mode: PolicyMode = PolicyMode.FIXED_TAU,
        arms_cfg: dict | None = None,
        broker: str = "localhost",
        port: int = 1883,
        client_id: str = "edge-pub",
        keepalive: int = 30,
        seed: int | None = None,
        mic: MicCfg | None = None,
        temp: TempCfg | None = None,
        rtc: RTCCfg | None = None,
        ui: UICfg | None = None,
        buttons: ButtonsCfg | None = None,
        link: LinkCfg | None = None,
    ):
        self.device_id = device_id
        self.profile = profile
        self.outbox_path = outbox_path
        self.run_dir = os.path.dirname(self.outbox_path) or "."
        self.run_id = os.path.basename(os.path.normpath(self.run_dir))
        self.mode = mode
        self.seed = seed
        self._arms_cfg = arms_cfg or {}
        self.broker = broker
        self.port = int(port)
        self.client_id = client_id
        self.keepalive = int(keepalive)
        self.mic_cfg = mic or MicCfg()
        self.temp_cfg = temp or TempCfg()
        self.rtc_cfg = rtc or RTCCfg()
        self.ui_cfg = ui or UICfg()
        self.buttons_cfg = buttons or ButtonsCfg()
        self.link_cfg = link or LinkCfg()
        self._tc_profiles_override = None

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
        self._lock = threading.Lock()
        self._mic_thread: Optional[threading.Thread] = None
        self._temp_thread: Optional[threading.Thread] = None
        self._rtc_device: DS3231 | None = None
        self._rtc_guardian: RTCGuardian | None = None
        self._ui_thread: Optional[threading.Thread] = None
        self._display = None
        self._buttons = None

        # UI/통계 집계
        self._status = StatusTracker(
            profile=self.profile,
            mode=self.mode,
            rate_window_s=self.ui_cfg.rate_window_s,
        )
        # 정책 실행기
        self._mic_policy: SensorPolicyRuntime | None = None
        self._temp_policy: SensorPolicyRuntime | None = None

    # ---------- 라이프사이클 ----------

    def start(self):
        self._install_signals()
        self._maybe_start_rtc()
        if self.link_cfg.apply_on_start:
            self._apply_link_profile()
        self.publisher.start()

        if self.mic_cfg.enable:
            self._mic_thread = threading.Thread(
                target=self._mic_loop, name="edge-mic", daemon=True
            )
            self._mic_thread.start()

        if self.temp_cfg.enable:
            self._temp_thread = threading.Thread(
                target=self._temp_loop, name="edge-temp", daemon=True
            )
            self._temp_thread.start()

        self._maybe_start_ui()
        self._maybe_start_buttons()

        logger.info(
            "edge_started mode=%s profile=%s (Ctrl+C to stop)",
            self.mode.value,
            self.profile.value,
        )
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
        if self._ui_thread and self._ui_thread.is_alive():
            self._ui_thread.join(timeout=2.0)
        if getattr(self, "_display", None):
            try:
                self._display.close()
            except Exception:
                pass
        if getattr(self, "_buttons", None):
            try:
                self._buttons.stop()
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
        logger.info("edge_stopped")

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
                logger.warning("rtc_unavailable error=%s", status.last_error)
                self._rtc_guardian = None
                if self._rtc_device is not None:
                    try:
                        self._rtc_device.close()
                    except Exception:
                        pass
                    self._rtc_device = None
                return
            drift = status.drift_seconds or 0.0
            logger.info(
                "rtc_sync rtc=%s drift_s=%.3f",
                status.rtc_time.isoformat(),
                float(drift),
            )
            if self.rtc_cfg.resync_interval_s > 0:
                self._rtc_guardian.start()
        except Exception:
            logger.exception("rtc_init_failed")
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

    def _maybe_start_ui(self):
        if not self.ui_cfg.enable or self._ui_thread is not None:
            return
        try:
            disp_cfg = DisplayConfig(
                kind=self.ui_cfg.kind,
                bus=self.ui_cfg.bus,
                address=self.ui_cfg.address,
                refresh_s=self.ui_cfg.refresh_s,
            )
            self._display = build_display(disp_cfg)
        except Exception:
            self._display = None
            logger.exception("ui_init_failed kind=%s bus=%s address=%s", self.ui_cfg.kind, self.ui_cfg.bus, self.ui_cfg.address)
            return
        if self._display is None:
            return
        self._ui_thread = threading.Thread(target=self._ui_loop, name="edge-ui", daemon=True)
        self._ui_thread.start()

    def _ui_loop(self):
        if self._display is None:
            return
        interval = max(0.2, float(self.ui_cfg.refresh_s))
        while not self._stop.is_set():
            try:
                pending = self.outbox.pending()
            except Exception:
                pending = 0
                logger.exception("ui_outbox_pending_failed")
            mqtt_ok = self.publisher.is_connected()
            snap = self._status.snapshot(mqtt_connected=mqtt_ok, outbox_pending=pending)
            try:
                self._display.show_snapshot(snap)
            except Exception:
                logger.exception("ui_render_failed")
            time.sleep(interval)
        try:
            self._display.close()
        except Exception:
            pass

    def _maybe_start_buttons(self):
        if self._buttons is not None:
            return
        self._buttons = build_buttons(
            ButtonsConfig(
                enable=self.buttons_cfg.enable,
                mode_pin=self.buttons_cfg.mode_pin,
                profile_pin=self.buttons_cfg.profile_pin,
                marker_pin=self.buttons_cfg.marker_pin,
                debounce_ms=self.buttons_cfg.debounce_ms,
            ),
            on_mode=self._cycle_mode,
            on_profile=self._cycle_profile,
            on_marker=self._emit_marker,
        )
        try:
            self._buttons.start()
        except Exception:
            logger.exception("buttons_init_failed")

    def _cycle_mode(self):
        with self._lock:
            modes = [PolicyMode.PERIODIC, PolicyMode.FIXED_TAU, PolicyMode.ADAPTIVE]
            cur_idx = modes.index(self.mode) if self.mode in modes else 0
            next_mode = modes[(cur_idx + 1) % len(modes)]
            if next_mode == PolicyMode.ADAPTIVE and not self._arms_cfg:
                logger.warning("buttons_mode_switch_rejected reason=no_arms_config")
                return
            self.mode = next_mode
            self._status.update_policy(mode=self.mode)
            logger.info("buttons_mode mode=%s", self.mode.value)
            self._refresh_policies()

    def _cycle_profile(self):
        with self._lock:
            profiles = [
                LinkProfile.SLOW_10KBPS,
                LinkProfile.DELAY_LOSS,
                LinkProfile.CELLULAR_VAR,
            ]
            cur_idx = profiles.index(self.profile) if self.profile in profiles else 0
            self.profile = profiles[(cur_idx + 1) % len(profiles)]
            self._status.update_policy(profile=self.profile)
            logger.info("buttons_profile profile=%s", self.profile.value)
            self._apply_link_profile()
            self._refresh_policies()

    def _emit_marker(self):
        ts = time.time_ns()
        payload = _json_dumps(
            {
                "ts": ts,
                "device_id": self.device_id,
                "type": "marker",
                "note": "button_press",
            }
        )
        try:
            self._status.record_payload(len(payload), ts)
            self.outbox.enqueue(
                f"marker/{self.device_id}", payload, qos=1, retain=False, created_ns=ts
            )
            logger.info("marker_emitted device_id=%s ts_ns=%s", self.device_id, ts)
        except Exception:
            logger.exception("marker_enqueue_failed device_id=%s", self.device_id)

    def _apply_link_profile(self):
        if not (self.link_cfg.apply_on_button or self.link_cfg.apply_on_start):
            return
        try:
            profiles_override = None
            if self.link_cfg.profiles_config:
                if self._tc_profiles_override is None:
                    try:
                        self._tc_profiles_override = tc_profiles.load_profiles_config(
                            self.link_cfg.profiles_config
                        )
                    except Exception:
                        logger.exception(
                            "tc_profiles_config_load_failed path=%s", self.link_cfg.profiles_config
                        )
                        self._tc_profiles_override = None
                profiles_override = self._tc_profiles_override
            tc_profiles.apply_profile(
                self.link_cfg.iface,
                self.profile.value,
                both=self.link_cfg.both,
                profiles=profiles_override,
            )
            logger.info(
                "tc_profile_applied iface=%s profile=%s both=%s",
                self.link_cfg.iface,
                self.profile.value,
                bool(self.link_cfg.both),
            )
        except Exception:
            logger.exception(
                "tc_apply_failed iface=%s profile=%s both=%s",
                self.link_cfg.iface,
                self.profile.value,
                bool(self.link_cfg.both),
            )

    def _refresh_policies(self):
        # 현재 모드/프로파일을 반영해 정책 실행기를 재생성
        if self.mic_cfg.enable:
            self._mic_policy = self._build_policy_runtime(
                sensor=SensorType.MIC_RMS,
                alpha=self.mic_cfg.alpha,
                tau=self.mic_cfg.tau,
                kbits=self.mic_cfg.kbits,
                heartbeat_s=self.mic_cfg.heartbeat_s,
                min_emit_ms=self.mic_cfg.min_emit_ms,
                nominal_period_s=self.mic_cfg.frame_ms / 1000.0,
            )
        if self.temp_cfg.enable:
            self._temp_policy = self._build_policy_runtime(
                sensor=SensorType.TEMP,
                alpha=self.temp_cfg.alpha,
                tau=self.temp_cfg.tau,
                kbits=self.temp_cfg.kbits,
                heartbeat_s=self.temp_cfg.heartbeat_s,
                min_emit_ms=self.temp_cfg.min_emit_ms,
                nominal_period_s=(
                    (1.0 / self.temp_cfg.sample_hz) if self.temp_cfg.sample_hz > 0 else 1.0
                ),
            )

    def _mic_loop(self):
        cfg = self.mic_cfg
        try:
            # 센서 준비
            self._mic_obj = MicRMS(
                device_id=self.device_id,
                sample_rate=cfg.sample_rate,
                frame_ms=cfg.frame_ms,
                backend=cfg.backend,
                arecord_device=cfg.arecord_device,
                sounddevice_device=cfg.sounddevice_device,
            )
            self._mic_policy = self._build_policy_runtime(
                sensor=SensorType.MIC_RMS,
                alpha=cfg.alpha,
                tau=cfg.tau,
                kbits=cfg.kbits,
                heartbeat_s=cfg.heartbeat_s,
                min_emit_ms=cfg.min_emit_ms,
                nominal_period_s=cfg.frame_ms / 1000.0,
            )
        except Exception as e:
            logger.exception("mic_init_failed")
            return
        logger.info(
            "mic_loop_started device=%s mode=%s alpha=%s tau=%s kbits=%s",
            self._mic_obj,
            self.mode.value,
            cfg.alpha,
            cfg.tau,
            cfg.kbits,
        )
        try:
            for s in self._mic_obj.stream(duration_s=None):
                if self._stop.is_set():
                    break
                self._status.update_mic(s.dbfs, s.clip_ratio)
                pending = self.outbox.pending()
                res = self._mic_policy.step(s, outbox_pending=pending)
                self._handle_step_result(res, label="mic", sensor=SensorType.MIC_RMS)
        except SystemExit:
            pass
        except Exception:
            logger.exception("mic_loop_error")
        finally:
            try:
                self._mic_obj.close()
            except Exception:
                pass
            logger.info("mic_loop_stopped")

    def _temp_loop(self):
        cfg = self.temp_cfg
        try:
            # 센서 준비
            self._temp_obj = TempSensor(
                device_id=self.device_id,
                backend=cfg.backend,
                sample_hz=cfg.sample_hz,
                w1_path=cfg.w1_path,
                sysfs_path=cfg.sysfs_path,
            )
            self._temp_policy = self._build_policy_runtime(
                sensor=SensorType.TEMP,
                alpha=cfg.alpha,
                tau=cfg.tau,
                kbits=cfg.kbits,
                heartbeat_s=cfg.heartbeat_s,
                min_emit_ms=cfg.min_emit_ms,
                nominal_period_s=(1.0 / cfg.sample_hz) if cfg.sample_hz > 0 else 1.0,
            )
        except Exception as e:
            logger.exception("temp_init_failed")
            return
        logger.info(
            "temp_loop_started device=%s mode=%s alpha=%s tau=%s kbits=%s",
            self._temp_obj,
            self.mode.value,
            cfg.alpha,
            cfg.tau,
            cfg.kbits,
        )
        try:
            for s in self._temp_obj.stream(duration_s=None):
                if self._stop.is_set():
                    break
                self._status.update_temp(s.celsius, s.valid)
                pending = self.outbox.pending()
                res = self._temp_policy.step(s, outbox_pending=pending)
                self._handle_step_result(res, label="temp", sensor=SensorType.TEMP)
        except SystemExit:
            pass
        except Exception:
            logger.exception("temp_loop_error")
        finally:
            try:
                self._temp_obj.close()
            except Exception:
                pass
            logger.info("temp_loop_stopped")

    def _handle_step_result(self, res: StepResult, *, label: str, sensor: SensorType) -> None:
        # 상태/UI 업데이트
        self._status.record_metrics(
            sensor=sensor,
            aoi_ms=res.aoi_ms,
            mae=res.mae_est,
            rate_bps=res.rate_bps,
        )
        if res.event is not None:
            try:
                payload = res.event.to_json_bytes()
                self._status.record_payload(len(payload), res.event.ts)
                self.outbox.enqueue(
                    res.event.mqtt_topic(),
                    payload,
                    qos=1,
                    retain=False,
                    created_ns=res.event.ts,
                )
            except Exception:
                logger.exception("outbox_enqueue_failed type=event label=%s sensor=%s", label, sensor.value)
        if res.decision is not None:
            if res.event is not None:
                self._maybe_log_policy_diag(sensor=sensor, seq=int(res.event.seq), decision=res.decision)
            try:
                payload = res.decision.to_json_bytes()
                self._status.record_payload(len(payload), res.decision.ts)
                self.outbox.enqueue(
                    res.decision.mqtt_topic(),
                    payload,
                    qos=1,
                    retain=False,
                    created_ns=res.decision.ts,
                )
            except Exception:
                logger.exception(
                    "outbox_enqueue_failed type=decision label=%s sensor=%s", label, sensor.value
                )

    def _maybe_log_policy_diag(self, *, sensor: SensorType, seq: int, decision) -> None:
        if decision is None:
            return
        if getattr(decision, "arm_id", None) is None:
            return
        if not logger.isEnabledFor(logging.DEBUG):
            return

        logger.debug(
            "policy_diag run_id=%s device_id=%s sensor=%s seq=%s arm_id=%s tau=%s kbits=%s "
            "safe_arm_forced=%s forced_reason=%s exploitation=%s exploration=%s score=%s ucb_alpha=%s "
            "reward_aoi=%s reward_mae=%s reward_rate=%s rate_limit_skips=%s "
            "t_predict_ms=%s t_decide_ms=%s t_observe_ms=%s t_step_ms=%s cpu_step_ms=%s maxrss_kb=%s",
            self.run_id,
            self.device_id,
            sensor.value,
            int(seq),
            getattr(decision, "arm_id", None),
            getattr(decision, "tau", None),
            getattr(decision, "kbits", None),
            getattr(decision, "safe_arm_forced", None),
            getattr(decision, "forced_reason", None),
            getattr(decision, "ucb_exploitation", None),
            getattr(decision, "ucb_exploration", None),
            getattr(decision, "ucb_score", None),
            getattr(decision, "ucb_alpha", None),
            getattr(decision, "reward_aoi", None),
            getattr(decision, "reward_mae", None),
            getattr(decision, "reward_rate", None),
            getattr(decision, "rate_limit_skips", None),
            getattr(decision, "t_predict_ms", None),
            getattr(decision, "t_decide_ms", None),
            getattr(decision, "t_observe_ms", None),
            getattr(decision, "t_step_ms", None),
            getattr(decision, "cpu_step_ms", None),
            getattr(decision, "maxrss_kb", None),
        )

    def _build_policy_runtime(
        self,
        *,
        sensor: SensorType,
        alpha: float,
        tau: float,
        kbits: int,
        heartbeat_s: float | None,
        min_emit_ms: int,
        nominal_period_s: float | None,
    ) -> SensorPolicyRuntime:
        diag_enabled = False
        if self.mode == PolicyMode.ADAPTIVE:
            diag_enabled = bool((self._arms_cfg.get("diagnostics") or {}).get("enabled", False))
        ewma_cfg = EWMAConfig(
            device_id=self.device_id,
            sensor=sensor,
            alpha=alpha,
            tau=tau,
            kbits=kbits,
            profile=self.profile,
            heartbeat_s=heartbeat_s,
            min_emit_interval_ms=min_emit_ms,
            bootstrap_emit=True,
            diagnostics_enabled=diag_enabled,
        )
        linucb_cfg = None
        if self.mode == PolicyMode.ADAPTIVE:
            linucb_cfg = self._make_linucb_config(sensor)
        return SensorPolicyRuntime(
            device_id=self.device_id,
            sensor=sensor,
            profile=self.profile,
            mode=self.mode,
            ewma_cfg=ewma_cfg,
            linucb_cfg=linucb_cfg,
            nominal_period_s=nominal_period_s,
        )

    def _make_linucb_config(self, sensor: SensorType):
        cfg = self._arms_cfg or {}
        arms = cfg.get("arms") or []
        if not arms:
            raise ValueError("adaptive mode requires arms in config (see configs/policy.yaml)")
        mae_scale = max(float(a.get("tau", 1.0)) for a in arms)
        if mae_scale <= 0:
            mae_scale = 1.0
        res_scale = mae_scale
        resvar_scale = mae_scale * mae_scale
        return load_linucb_config(
            cfg,
            device_id=self.device_id,
            sensor=sensor,
            profile=self.profile,
            seed=self.seed,
            mae_scale=mae_scale,
            res_scale=res_scale,
            resvar_scale=resvar_scale,
        )

    # ---------- 기타 ----------

    def _install_signals(self):
        def _h(signum, frame):
            logger.info("edge_signal=%s stopping", signum)
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

def _opt_path(val: str | None) -> str | None:
    if val is None:
        return None
    sv = str(val).strip()
    if not sv:
        return None
    if sv.lower() in {"none", "null"}:
        return None
    return sv


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    # device-config를 먼저 파싱해, 나머지 옵션의 default를 동적으로 구성한다.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--device-config", default=None, help="device YAML path (e.g., configs/device.yaml)")
    pre.add_argument("--device-id", default=None)
    pre_args, _ = pre.parse_known_args(argv)

    device_cfg = None
    device_config_path = _opt_path(pre_args.device_config)
    if device_config_path:
        device_cfg = _load_device_yaml(device_config_path)

    device_id_default = (
        str(pre_args.device_id)
        if pre_args.device_id
        else (device_cfg.device_id if device_cfg is not None else None)
    )
    broker_default = device_cfg.mqtt.host if device_cfg is not None else "localhost"
    port_default = int(device_cfg.mqtt.port) if device_cfg is not None else 1883
    mic_enable_default = bool(device_cfg.sensors.mic is not None) if device_cfg is not None else False
    temp_enable_default = bool(device_cfg.sensors.temp is not None) if device_cfg is not None else False
    mic_sr_default = (
        int(device_cfg.sensors.mic.samplerate)
        if device_cfg is not None and device_cfg.sensors.mic is not None
        else 16000
    )
    mic_frame_ms_default = (
        int(device_cfg.sensors.mic.frame_ms)
        if device_cfg is not None and device_cfg.sensors.mic is not None
        else 100
    )
    temp_hz_default = (
        float(device_cfg.sensors.temp.period_hz)
        if device_cfg is not None and device_cfg.sensors.temp is not None
        else 1.0
    )
    ui_enable_default = bool(device_cfg.ui.enabled) if device_cfg is not None and device_cfg.ui else False
    ui_kind_default = "auto"
    if device_cfg is not None and device_cfg.ui is not None:
        backend = str(device_cfg.ui.backend).strip().lower()
        # configs/device.yaml은 "lcd|console" 형태를 사용한다.
        if backend in {"lcd", "lcd1602"}:
            ui_kind_default = "lcd1602"
        elif backend in {"console"}:
            ui_kind_default = "console"
        elif backend in {"ssd1306", "auto"}:
            ui_kind_default = backend

    p = argparse.ArgumentParser(description="Edge daemon: sensors → EWMA(τ) → Outbox → MQTT QoS1")
    # 공통
    add_logging_cli_args(p)
    p.add_argument(
        "--device-config",
        default=device_config_path,
        help="device YAML path (maps to broker/port/sensors/ui defaults)",
    )
    p.add_argument("--device-id", default=device_id_default)
    p.add_argument(
        "--profile",
        choices=[e.value for e in LinkProfile],
        default=LinkProfile.SLOW_10KBPS.value,
    )
    p.add_argument(
        "--mode",
        choices=[e.value for e in PolicyMode],
        default=PolicyMode.FIXED_TAU.value,
        help="policy mode: periodic | fixed_tau | adaptive (LinUCB)",
    )
    p.add_argument("--arms", default="configs/policy.yaml", help="(adaptive) arms YAML path")
    p.add_argument("--run-dir", default=None, help="기록 루트(artifacts/<ts>_<device_id> 기본)")
    p.add_argument("--broker", default=broker_default)
    p.add_argument("--port", type=int, default=port_default)
    p.add_argument("--client-id", default="edge-pub")
    p.add_argument("--keepalive", type=int, default=30)
    p.add_argument("--seed", type=int, default=None, help="random seed (reproducibility)")

    # Outbox
    p.add_argument("--outbox", default=None, help="SQLite 경로(기본: <run-dir>/outbox.sqlite)")

    # RTC 옵션
    p.add_argument("--rtc-enable", action="store_true", default=False, help="DS3231 RTC 가드 활성화")
    p.add_argument("--rtc-bus", type=int, default=1, help="DS3231 I2C bus 번호")
    p.add_argument(
        "--rtc-address", type=_parse_int_auto, default=0x68, help="DS3231 I2C 주소(예: 0x68)"
    )
    p.add_argument("--rtc-drift-guard", type=float, default=2.0, help="RTC와 시스템간 허용 오차(초)")
    p.add_argument("--rtc-resync", type=float, default=900.0, help="재동기화 주기(초), <=0이면 1회만")
    p.add_argument("--rtc-push-system", action="store_true", help="시스템 시간이 앞설 때 RTC에 기록")

    # MIC 옵션
    mic_group = p.add_mutually_exclusive_group()
    mic_group.add_argument(
        "--mic-enable", dest="mic_enable", action="store_true", help="마이크 스트림 활성화"
    )
    mic_group.add_argument(
        "--mic-disable", dest="mic_enable", action="store_false", help="마이크 스트림 비활성화"
    )
    p.set_defaults(mic_enable=mic_enable_default)
    p.add_argument("--mic-backend", choices=["auto", "sounddevice", "arecord"], default="auto")
    p.add_argument("--mic-arecord-device", default=None)
    p.add_argument("--mic-sd-device", default=None)
    p.add_argument("--mic-sr", type=int, default=mic_sr_default)
    p.add_argument("--mic-frame-ms", type=int, default=mic_frame_ms_default)
    p.add_argument("--mic-alpha", type=float, default=0.2)
    p.add_argument("--mic-tau", type=float, default=3.0)
    p.add_argument("--mic-kbits", type=int, default=6)
    p.add_argument("--mic-heartbeat", type=float, default=10.0)
    p.add_argument("--mic-min-emit-ms", type=int, default=0)

    # TEMP 옵션
    temp_group = p.add_mutually_exclusive_group()
    temp_group.add_argument(
        "--temp-enable", dest="temp_enable", action="store_true", help="온도 스트림 활성화"
    )
    temp_group.add_argument(
        "--temp-disable", dest="temp_enable", action="store_false", help="온도 스트림 비활성화"
    )
    p.set_defaults(temp_enable=temp_enable_default)
    p.add_argument("--temp-backend", choices=["auto", "w1", "sysfs", "mock"], default="auto")
    p.add_argument("--temp-hz", type=float, default=temp_hz_default)
    p.add_argument("--temp-alpha", type=float, default=0.5)
    p.add_argument("--temp-tau", type=float, default=0.2)
    p.add_argument("--temp-kbits", type=int, default=8)
    p.add_argument("--temp-heartbeat", type=float, default=10.0)
    p.add_argument("--temp-min-emit-ms", type=int, default=0)
    p.add_argument("--temp-w1-path", default=None)
    p.add_argument("--temp-sysfs-path", default=None)

    # UI 옵션
    ui_group = p.add_mutually_exclusive_group()
    ui_group.add_argument(
        "--ui-enable", dest="ui_enable", action="store_true", help="I2C LCD/OLED 상태 표시"
    )
    ui_group.add_argument(
        "--ui-disable", dest="ui_enable", action="store_false", help="UI 비활성화"
    )
    p.set_defaults(ui_enable=ui_enable_default)
    p.add_argument(
        "--ui-kind", choices=["auto", "lcd1602", "ssd1306", "console"], default=ui_kind_default
    )
    p.add_argument("--ui-bus", type=int, default=1, help="I2C bus 번호 (기본 1)")
    p.add_argument(
        "--ui-address", type=_parse_int_auto, default=None, help="LCD/OLED I2C 주소 override"
    )
    p.add_argument("--ui-refresh", type=float, default=1.0, help="표시 갱신 주기(초)")
    p.add_argument("--ui-rate-window", type=float, default=10.0, help="전송률 이동 평균 윈도우(초)")

    # 버튼/링크 제어
    p.add_argument(
        "--buttons-enable",
        dest="buttons_enable",
        action="store_true",
        help="GPIO 버튼(모드/링크/마커) 사용",
    )
    p.add_argument(
        "--buttons-disable",
        dest="buttons_enable",
        action="store_false",
        help="GPIO 버튼 비활성화",
    )
    p.set_defaults(buttons_enable=False)
    p.add_argument("--btn-mode-pin", type=int, default=17, help="정책 모드 버튼 BCM 핀")
    p.add_argument("--btn-profile-pin", type=int, default=27, help="링크 프로파일 버튼 BCM 핀")
    p.add_argument("--btn-marker-pin", type=int, default=22, help="마커 버튼 BCM 핀")
    p.add_argument("--btn-debounce-ms", type=int, default=200, help="버튼 디바운스(ms)")
    p.add_argument("--tc-iface", default="eth0", help="tc 적용할 인터페이스 (버튼 프로파일 변경 시)")
    p.add_argument("--tc-both", action="store_true", help="ingress(ifb0)도 shaping")
    p.add_argument(
        "--tc-apply-on-button",
        action="store_true",
        help="버튼 프로파일 변경 시 tc 즉시 적용(root 필요)",
    )
    p.add_argument(
        "--tc-apply-on-start",
        action="store_true",
        help="시작 시 현재 profile을 tc로 1회 적용(권한 필요)",
    )
    p.add_argument(
        "--tc-profiles-config",
        default=None,
        help="YAML path for overriding tc profiles (e.g., configs/link_profiles.yaml)",
    )

    args = p.parse_args(argv)

    # device-config를 지정했는데, pre-parse에서 로드 실패한 경우를 대비해 본 파싱 후에도 로드.
    # (예: `--device-config`가 후반 옵션에 의해 덮였을 때)
    args.device_config = _opt_path(args.device_config)
    if args.device_config and device_cfg is None:
        device_cfg = _load_device_yaml(args.device_config)
    if args.device_id is None:
        if device_cfg is not None:
            args.device_id = device_cfg.device_id
        else:
            p.error("--device-id is required (or provide --device-config)")

    return args


def main(argv: list[str] | None = None):
    args = parse_args(argv)
    setup_logging_from_args(args)

    seed = args.seed
    if seed is None:
        seed_env = os.environ.get("SEMUP_SEED")
        if seed_env:
            try:
                seed = int(seed_env)
            except ValueError:
                seed = None
    if seed is None:
        seed = 0
    _seed_everything(int(seed))

    policy_mode = PolicyMode(args.mode)
    profile = LinkProfile(args.profile)
    arms_cfg: dict | None = None
    if policy_mode == PolicyMode.ADAPTIVE:
        logger.info("adaptive_seed seed=%s", seed)
        arms_cfg = _load_policy_yaml(args.arms)

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

    ui_cfg = UICfg(
        enable=bool(args.ui_enable),
        kind=args.ui_kind,
        bus=args.ui_bus,
        address=args.ui_address,
        refresh_s=args.ui_refresh,
        rate_window_s=args.ui_rate_window,
    )

    buttons_cfg = ButtonsCfg(
        enable=bool(args.buttons_enable),
        mode_pin=args.btn_mode_pin,
        profile_pin=args.btn_profile_pin,
        marker_pin=args.btn_marker_pin,
        debounce_ms=args.btn_debounce_ms,
    )

    link_cfg = LinkCfg(
        iface=args.tc_iface,
        both=bool(args.tc_both),
        apply_on_button=bool(args.tc_apply_on_button),
        apply_on_start=bool(args.tc_apply_on_start),
        profiles_config=args.tc_profiles_config,
    )

    if not mic_cfg.enable and not temp_cfg.enable:
        logger.error(
            "at least one sensor must be enabled (--mic-enable / --temp-enable)"
        )
        raise SystemExit(2)

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
        profile=profile,
        mode=policy_mode,
        arms_cfg=arms_cfg,
        outbox_path=outbox_path,
        broker=args.broker,
        port=args.port,
        client_id=args.client_id,
        keepalive=args.keepalive,
        seed=int(seed),
        mic=mic_cfg,
        temp=temp_cfg,
        rtc=rtc_cfg,
        ui=ui_cfg,
        buttons=buttons_cfg,
        link=link_cfg,
    )
    try:
        daemon.start()
    finally:
        daemon.stop()


# 작은 실수 방지: heartbeat 0/음수 → 비활성(None) 변환용 헬퍼 (가독성 위해 분리)
def _load_policy_yaml(path: str) -> dict:
    try:
        return load_policy_config_dict(path)
    except Exception:
        logger.exception("invalid_arms_config path=%s", path)
        raise SystemExit(2) from None

def _load_device_yaml(path: str):
    try:
        return load_device_config(path)
    except Exception:
        logger.exception("invalid_device_config path=%s", path)
        raise SystemExit(2) from None


def _hb_none(x: float | None) -> float | None:
    if x is None:
        return None
    try:
        xv = float(x)
    except Exception:
        return None
    return None if xv <= 0 else xv


def _seed_everything(seed: int) -> None:
    import random

    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        return None


def _parse_int_auto(val: str) -> int:
    try:
        return int(str(val), 0)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"invalid integer literal: {val}") from e


if __name__ == "__main__":
    main()
