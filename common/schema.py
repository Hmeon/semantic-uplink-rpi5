# common/schema.py
# Python 3.10+
# 목적: MQTT 메시지 스키마(Event/PolicyDecision)를 단일 출처로 정의하고,
#       엄격한 검증 + 직렬화 + MQTT PUBLISH 크기 산정까지 지원한다.
# - 과제/동결안의 스키마/토픽/프로파일/정의와 정확히 일치. (헤더 포함 Rate 산정과 정합)
# - 외부 의존성 없음(표준 라이브러리만).  [과제 제안서 준수]

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional

# mqttutil은 schema를 참조하지 않으므로 순환 의존 없음
try:
    from .mqttutil import mqtt_v311_publish_size  # 패키지 내부 상대 import
except Exception:  # collector 등에서만 사용하므로, 없으면 기능만 비활성
    mqtt_v311_publish_size = None  # type: ignore[assignment]

__all__ = [
    "SensorType", "PolicyMode", "LinkProfile",
    "EventMsg", "PolicyDecisionMsg",
    "INT64_MAX", "UINT64_MAX", "SCHEMA_VERSION"
]

SCHEMA_VERSION: str = "1.0.0"

INT64_MAX: int = (1 << 63) - 1
UINT64_MAX: int = (1 << 64) - 1

# -------------------- Enums (문자열 값 고정) --------------------

class SensorType(str, Enum):
    MIC_RMS = "mic_rms"
    TEMP = "temp"

class PolicyMode(str, Enum):
    PERIODIC = "periodic"
    FIXED_TAU = "fixed_tau"
    ADAPTIVE = "adaptive"

class LinkProfile(str, Enum):
    SLOW_10KBPS = "slow_10kbps"
    DELAY_LOSS = "delay_loss"
    CELLULAR_VAR = "cellular_var"

# -------------------- 공통 유틸 --------------------

def _ensure_finite(name: str, v: float) -> float:
    try:
        f = float(v)
    except Exception as e:
        raise TypeError(f"{name} must be float-like") from e
    if not math.isfinite(f):
        raise ValueError(f"{name} must be finite")
    return f

def _ensure_nonneg_int(name: str, v: int) -> int:
    try:
        i = int(v)
    except Exception as e:
        raise TypeError(f"{name} must be int") from e
    if i < 0:
        raise ValueError(f"{name} must be >= 0")
    return i

def _ensure_nonempty_str(name: str, s: str) -> str:
    if not isinstance(s, str):
        raise TypeError(f"{name} must be str")
    if s == "":
        raise ValueError(f"{name} must be non-empty")
    return s

def _enum_from(value: str | Enum, enum_cls: Any, name: str) -> Enum:
    if isinstance(value, enum_cls):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str or {enum_cls.__name__}")
    try:
        return enum_cls(value)
    except ValueError:
        choices = ", ".join([e.value for e in enum_cls])  # type: ignore[attr-defined]
        raise ValueError(f"{name} invalid: {value!r} (choices: {choices})")

# -------------------- Event --------------------

@dataclass(slots=True, frozen=True)
class EventMsg:
    """
    업링크 이벤트 메시지(엣지→브로커).
    필드명/타입은 스키마 동결안과 동일:
      ts(int64 ns epoch), seq(u64), device_id(str), sensor("mic_rms"|"temp"),
      val(float), pred(float), res(float), tau(float), kbits(int),
      aoi_ms(Optional[int]), profile(str), policy("periodic"|"fixed_tau"|"adaptive")
    JSON 직렬화 시 None 필드는 생략(바이트 절약).
    """
    ts: int
    seq: int
    device_id: str
    sensor: SensorType
    val: float
    pred: float
    res: float
    tau: float
    kbits: int
    profile: LinkProfile
    policy: PolicyMode
    aoi_ms: Optional[int] = None  # 선택 필드(로그용)

    # ---- 검증/정규화 ----
    def __post_init__(self):
        # 타입/범위 강제(불변 dataclass라서 object.__setattr__ 사용)
        ts = int(self.ts);  seq = int(self.seq)
        if not (0 <= ts <= INT64_MAX): raise ValueError("ts out of int64 range")
        if not (0 <= seq <= UINT64_MAX): raise ValueError("seq out of uint64 range")

        device_id = _ensure_nonempty_str("device_id", self.device_id)
        # 토픽 안전: 슬래시 금지(토픽 구분자와 충돌)
        if "/" in device_id:
            raise ValueError("device_id must not contain '/'")

        sensor = _enum_from(self.sensor, SensorType, "sensor")
        policy = _enum_from(self.policy, PolicyMode, "policy")
        profile = _enum_from(self.profile, LinkProfile, "profile")

        val = _ensure_finite("val", self.val)
        pred = _ensure_finite("pred", self.pred)
        res = _ensure_finite("res", self.res)
        tau = _ensure_finite("tau", self.tau)

        kbits = int(self.kbits)
        if not (1 <= kbits <= 16):
            # PoC 범위: 1~16bit 양자화(동결안의 단순 격자와 정합)
            raise ValueError("kbits must be in [1, 16]")

        aoi_ms = None if self.aoi_ms is None else _ensure_nonneg_int("aoi_ms", self.aoi_ms)

        object.__setattr__(self, "ts", ts)
        object.__setattr__(self, "seq", seq)
        object.__setattr__(self, "device_id", device_id)
        object.__setattr__(self, "sensor", sensor)
        object.__setattr__(self, "policy", policy)
        object.__setattr__(self, "profile", profile)
        object.__setattr__(self, "val", val)
        object.__setattr__(self, "pred", pred)
        object.__setattr__(self, "res", res)
        object.__setattr__(self, "tau", tau)
        object.__setattr__(self, "kbits", kbits)
        object.__setattr__(self, "aoi_ms", aoi_ms)

    # ---- 직렬화/역직렬화 ----
    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "ts": self.ts,
            "seq": self.seq,
            "device_id": self.device_id,
            "sensor": self.sensor.value,
            "val": float(self.val),
            "pred": float(self.pred),
            "res": float(self.res),
            "tau": float(self.tau),
            "kbits": int(self.kbits),
            "profile": self.profile.value,
            "policy": self.policy.value,
        }
        if self.aoi_ms is not None:
            d["aoi_ms"] = int(self.aoi_ms)
        return d

    def to_json_bytes(self) -> bytes:
        # 공백 없는 JSON (Rate 산정시 payload_len 최소화)
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EventMsg":
        # 필수 필드 확인
        required = ("ts", "seq", "device_id", "sensor", "val", "pred", "res", "tau", "kbits", "profile", "policy")
        missing = [k for k in required if k not in d]
        if missing:
            raise ValueError(f"missing fields: {missing}")
        return cls(
            ts=int(d["ts"]),
            seq=int(d["seq"]),
            device_id=str(d["device_id"]),
            sensor=d["sensor"],  # Enum 변환은 __post_init__에서
            val=float(d["val"]),
            pred=float(d["pred"]),
            res=float(d["res"]),
            tau=float(d["tau"]),
            kbits=int(d["kbits"]),
            profile=d["profile"],
            policy=d["policy"],
            aoi_ms=None if "aoi_ms" not in d or d["aoi_ms"] is None else int(d["aoi_ms"]),
        )

    @classmethod
    def from_json_bytes(cls, b: bytes) -> "EventMsg":
        try:
            d = json.loads(b.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"invalid JSON: {e}") from e
        return cls.from_dict(d)

    # ---- 토픽/크기 ----
    def mqtt_topic(self) -> str:
        # edge/{device_id}/{sensor}/event
        return f"edge/{self.device_id}/{self.sensor.value}/event"

    def estimated_mqtt_size(self, qos: int = 1) -> int:
        """헤더 포함 MQTT v3.1.1 PUBLISH 총 바이트 수(브로커 수신 기준)."""
        if mqtt_v311_publish_size is None:
            raise RuntimeError("mqtt_v311_publish_size unavailable")
        payload_len = len(self.to_json_bytes())
        return mqtt_v311_publish_size(self.mqtt_topic(), payload_len, qos=qos)

# -------------------- PolicyDecision --------------------

@dataclass(slots=True, frozen=True)
class PolicyDecisionMsg:
    """
    정책 결정 메시지(엣지→브로커).
    필드:
      ts(int64 ns), device_id(str),
      state_aoi(float), state_res(float), state_res_var(float),
      state_loss(float in [0,1]), state_q_len(int>=0),
      tau(float), kbits(int[1..16]), reward(float)
    """
    ts: int
    device_id: str
    state_aoi: float
    state_res: float
    state_res_var: float
    state_loss: float
    state_q_len: int
    tau: float
    kbits: int
    reward: float

    def __post_init__(self):
        ts = int(self.ts)
        if not (0 <= ts <= INT64_MAX):
            raise ValueError("ts out of int64 range")
        device_id = _ensure_nonempty_str("device_id", self.device_id)
        if "/" in device_id:
            raise ValueError("device_id must not contain '/'")

        state_aoi = _ensure_finite("state_aoi", self.state_aoi)
        state_res = _ensure_finite("state_res", self.state_res)
        state_res_var = _ensure_finite("state_res_var", self.state_res_var)
        if state_res_var < 0:
            raise ValueError("state_res_var must be >= 0")

        state_loss = _ensure_finite("state_loss", self.state_loss)
        if not (0.0 <= state_loss <= 1.0):
            raise ValueError("state_loss must be in [0, 1]")

        state_q_len = _ensure_nonneg_int("state_q_len", self.state_q_len)

        tau = _ensure_finite("tau", self.tau)
        kbits = int(self.kbits)
        if not (1 <= kbits <= 16):
            raise ValueError("kbits must be in [1, 16]")

        reward = _ensure_finite("reward", self.reward)

        object.__setattr__(self, "ts", ts)
        object.__setattr__(self, "device_id", device_id)
        object.__setattr__(self, "state_aoi", state_aoi)
        object.__setattr__(self, "state_res", state_res)
        object.__setattr__(self, "state_res_var", state_res_var)
        object.__setattr__(self, "state_loss", state_loss)
        object.__setattr__(self, "state_q_len", state_q_len)
        object.__setattr__(self, "tau", tau)
        object.__setattr__(self, "kbits", kbits)
        object.__setattr__(self, "reward", reward)

    # 직렬화/역직렬화
    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts,
            "device_id": self.device_id,
            "state_aoi": float(self.state_aoi),
            "state_res": float(self.state_res),
            "state_res_var": float(self.state_res_var),
            "state_loss": float(self.state_loss),
            "state_q_len": int(self.state_q_len),
            "tau": float(self.tau),
            "kbits": int(self.kbits),
            "reward": float(self.reward),
        }

    def to_json_bytes(self) -> bytes:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PolicyDecisionMsg":
        required = ("ts", "device_id", "state_aoi", "state_res", "state_res_var",
                    "state_loss", "state_q_len", "tau", "kbits", "reward")
        missing = [k for k in required if k not in d]
        if missing:
            raise ValueError(f"missing fields: {missing}")
        return cls(
            ts=int(d["ts"]),
            device_id=str(d["device_id"]),
            state_aoi=float(d["state_aoi"]),
            state_res=float(d["state_res"]),
            state_res_var=float(d["state_res_var"]),
            state_loss=float(d["state_loss"]),
            state_q_len=int(d["state_q_len"]),
            tau=float(d["tau"]),
            kbits=int(d["kbits"]),
            reward=float(d["reward"]),
        )

    @classmethod
    def from_json_bytes(cls, b: bytes) -> "PolicyDecisionMsg":
        try:
            d = json.loads(b.decode("utf-8"))
        except Exception as e:
            raise ValueError(f"invalid JSON: {e}") from e
        return cls.from_dict(d)

    # 토픽/크기
    def mqtt_topic(self) -> str:
        # policy/{device_id}/decision
        return f"policy/{self.device_id}/decision"

    def estimated_mqtt_size(self, qos: int = 1) -> int:
        if mqtt_v311_publish_size is None:
            raise RuntimeError("mqtt_v311_publish_size unavailable")
        payload_len = len(self.to_json_bytes())
        return mqtt_v311_publish_size(self.mqtt_topic(), payload_len, qos=qos)
