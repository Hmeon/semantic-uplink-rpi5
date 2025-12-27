# common/schema.py
# Python 3.10+
# 목적: MQTT 메시지 스키마(Event/PolicyDecision)를 단일 출처로 정의하고,
#       엄격한 검증 + 직렬화 + MQTT PUBLISH 크기 산정까지 지원한다.
# - 과제/동결안의 스키마/토픽/프로파일/정의와 정확히 일치. (헤더 포함 Rate 산정과 정합)
# - 직렬화는 표준 라이브러리 기반이며, 설치된 경우 msgspec로 가속한다.  [과제 제안서 준수]

"""Shared MQTT message schemas and validation utilities.

Defines Event/PolicyDecision payloads, validates fields at construction time,
and provides JSON serialization plus MQTT size estimation helpers. These
schemas are a compatibility boundary with the collector/analyzer pipeline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any

# mqttutil은 schema를 참조하지 않으므로 순환 의존 없음
try:
    from .mqttutil import mqtt_v311_publish_size  # 패키지 내부 상대 import
except Exception:  # collector 등에서만 사용하므로, 없으면 기능만 비활성
    mqtt_v311_publish_size = None  # type: ignore[assignment]

from .jsonutil import dumps as _json_dumps
from .jsonutil import loads as _json_loads

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
    """Supported sensor identifiers for event topics and payloads.

    Args:
        None.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Enum values must remain stable for topic/schema compatibility.

    Failure Modes:
        - None.
    """
    MIC_RMS = "mic_rms"
    TEMP = "temp"

class PolicyMode(str, Enum):
    """Policy mode identifiers embedded in EventMsg.

    Args:
        None.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Enum values must remain stable for downstream analysis.

    Failure Modes:
        - None.
    """
    PERIODIC = "periodic"
    FIXED_TAU = "fixed_tau"
    ADAPTIVE = "adaptive"

class LinkProfile(str, Enum):
    """Network profile identifiers used in experiments.

    Args:
        None.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Enum values must remain stable for config/profile matching.

    Failure Modes:
        - None.
    """
    SLOW_10KBPS = "slow_10kbps"
    DELAY_LOSS = "delay_loss"
    CELLULAR_VAR = "cellular_var"
    LORA_SF10 = "lora_sf10"
    LORA_SF12 = "lora_sf12"

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
    """Event message emitted by the edge (uplink).

    Args:
        ts: Epoch timestamp in nanoseconds.
        seq: Monotonic per-device sequence number.
        device_id: Device identifier (must not include '/').
        sensor: Sensor type identifier.
        val: Quantized sensor value.
        pred: Predictor output for the same sample.
        res: Residual error (val - pred).
        tau: Sampling or decision interval in seconds.
        kbits: Quantization bit width (1..16).
        profile: Link profile identifier.
        policy: Policy mode identifier.
        aoi_ms: Optional AoI in milliseconds for diagnostics.
        event_reason: Optional emission reason tag.

    Returns:
        None.

    Raises:
        ValueError: If numeric ranges are violated (e.g., kbits out of range).
        TypeError: If fields cannot be coerced into the required types.

    Side Effects:
        - None.

    Contract:
        - Fields must match the frozen schema for downstream compatibility.
        - `device_id` must not contain '/' to preserve topic structure.
        - JSON serialization omits optional None fields to reduce payload size.

    Failure Modes:
        - Validation raises ValueError/TypeError on invalid values.
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
    aoi_ms: int | None = None  # 선택 필드(로그용)
    event_reason: str | None = None

    # ---- 검증/정규화 ----
    def __post_init__(self):
        # 타입/범위 강제(불변 dataclass라서 object.__setattr__ 사용)
        ts = int(self.ts)
        seq = int(self.seq)
        if not (0 <= ts <= INT64_MAX):
            raise ValueError("ts out of int64 range")
        if not (0 <= seq <= UINT64_MAX):
            raise ValueError("seq out of uint64 range")

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
        event_reason = (
            None
            if self.event_reason is None
            else _ensure_nonempty_str("event_reason", self.event_reason)
        )

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
        object.__setattr__(self, "event_reason", event_reason)

    # ---- 직렬화/역직렬화 ----
    def to_dict(self) -> dict[str, Any]:
        """Serialize the message to a JSON-ready dict.

        Args:
            None.

        Returns:
            Dict with enum values rendered as strings.

        Raises:
            None.

        Side Effects:
            - None.

        Contract:
            - Optional fields are omitted when None.

        Failure Modes:
            - None.
        """
        d: dict[str, Any] = {
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
        if self.event_reason is not None:
            d["event_reason"] = str(self.event_reason)
        return d

    def to_json_bytes(self) -> bytes:
        """Serialize the message to compact JSON bytes.

        Args:
            None.

        Returns:
            UTF-8 encoded JSON without extra whitespace.

        Raises:
            ValueError: If serialization fails in the JSON backend.

        Side Effects:
            - None.

        Contract:
            - Uses the compact JSON backend to minimize payload length.

        Failure Modes:
            - JSON backend errors propagate as ValueError.
        """
        # 공백 없는 JSON (Rate 산정시 payload_len 최소화)
        return _json_dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EventMsg:
        """Construct an EventMsg from a dict payload.

        Args:
            d: Parsed JSON mapping.

        Returns:
            EventMsg instance with validated fields.

        Raises:
            KeyError: If required fields are missing.
            ValueError: If field values are out of allowed ranges.
            TypeError: If field types are incompatible.

        Side Effects:
            - None.

        Contract:
            - Required keys must be present in the mapping.

        Failure Modes:
            - Raises on malformed or incomplete payloads.
        """
        # 필수 필드 확인
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
            event_reason=(
                None
                if "event_reason" not in d or d["event_reason"] is None
                else str(d["event_reason"])
            ),
        )

    @classmethod
    def from_json_bytes(cls, b: bytes) -> EventMsg:
        """Parse JSON bytes into an EventMsg instance.

        Args:
            b: UTF-8 encoded JSON bytes.

        Returns:
            Validated EventMsg instance.

        Raises:
            ValueError: If JSON parsing fails or payload is not an object.

        Side Effects:
            - None.

        Contract:
            - Assumes the payload follows EventMsg JSON schema.

        Failure Modes:
            - JSON decoding failures surface as ValueError.
        """
        try:
            d = _json_loads(b)
        except Exception as e:
            raise ValueError(f"invalid JSON: {e}") from e
        if not isinstance(d, dict):
            raise ValueError("invalid JSON: expected an object")
        return cls.from_dict(d)

    # ---- 토픽/크기 ----
    def mqtt_topic(self) -> str:
        """Return the MQTT topic for this event.

        Args:
            None.

        Returns:
            Topic string formatted as `edge/{device_id}/{sensor}/event`.

        Raises:
            None.

        Side Effects:
            - None.

        Contract:
            - `device_id` must be slash-free to preserve topic hierarchy.

        Failure Modes:
            - None.
        """
        # edge/{device_id}/{sensor}/event
        return f"edge/{self.device_id}/{self.sensor.value}/event"

    def estimated_mqtt_size(self, qos: int = 1) -> int:
        """Estimate MQTT v3.1.1 publish size including headers.

        Args:
            qos: MQTT QoS level used to compute header sizing.

        Returns:
            Total bytes for a PUBLISH packet at the given QoS.

        Raises:
            RuntimeError: If the MQTT size calculator is unavailable.

        Side Effects:
            - None.

        Contract:
            - Uses `mqtt_v311_publish_size` for protocol-accurate sizing.

        Failure Modes:
            - Raises if the helper is missing in minimal environments.
        """
        if mqtt_v311_publish_size is None:
            raise RuntimeError("mqtt_v311_publish_size unavailable")
        payload_len = len(self.to_json_bytes())
        return mqtt_v311_publish_size(self.mqtt_topic(), payload_len, qos=qos)

# -------------------- PolicyDecision --------------------

@dataclass(slots=True, frozen=True)
class PolicyDecisionMsg:
    """Policy decision message emitted by the edge.

    Args:
        ts: Epoch timestamp in nanoseconds.
        device_id: Device identifier (must not include '/').
        state_aoi: AoI estimate in milliseconds.
        state_res: Residual error estimate.
        state_res_var: Residual variance estimate (>= 0).
        state_loss: Loss estimate in [0, 1].
        state_q_len: Outbox queue length (>= 0).
        tau: Selected sampling interval in seconds.
        kbits: Selected quantization bit width (1..16).
        reward: Scalar reward used by the policy.
        arm_id: Optional arm index for diagnostics.
        safe_arm_forced: Optional flag indicating a safety override.
        forced_reason: Optional reason string for a safety override.
        ucb_exploitation: Optional UCB exploitation term.
        ucb_exploration: Optional UCB exploration term.
        ucb_score: Optional total UCB score.
        ucb_alpha: Optional UCB alpha (must be > 0 if present).
        reward_aoi: Optional AoI component of the reward.
        reward_mae: Optional MAE component of the reward.
        reward_rate: Optional rate component of the reward.
        rate_limit_skips: Optional count of rate-limit skips.
        t_predict_ms: Optional predictor timing in milliseconds.
        t_decide_ms: Optional decision timing in milliseconds.
        t_observe_ms: Optional observe timing in milliseconds.
        t_step_ms: Optional total step timing in milliseconds.
        cpu_step_ms: Optional CPU time for the step in milliseconds.
        maxrss_kb: Optional max RSS during the step in KB.

    Returns:
        None.

    Raises:
        ValueError: If numeric ranges are violated.
        TypeError: If fields cannot be coerced into required types.

    Side Effects:
        - None.

    Contract:
        - `device_id` must not contain '/' to preserve topic structure.
        - Optional diagnostics are omitted from JSON when None.

    Failure Modes:
        - Validation raises ValueError/TypeError on invalid values.
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
    # --- optional diagnostics (enable via policy config) ---
    arm_id: int | None = None
    safe_arm_forced: bool | None = None
    forced_reason: str | None = None
    ucb_exploitation: float | None = None
    ucb_exploration: float | None = None
    ucb_score: float | None = None
    ucb_alpha: float | None = None  # allows deriving uncertainty: u = exploration / alpha
    reward_aoi: float | None = None
    reward_mae: float | None = None
    reward_rate: float | None = None
    rate_limit_skips: int | None = None
    t_predict_ms: float | None = None
    t_decide_ms: float | None = None
    t_observe_ms: float | None = None
    t_step_ms: float | None = None
    cpu_step_ms: float | None = None
    maxrss_kb: float | None = None

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

        arm_id = None if self.arm_id is None else _ensure_nonneg_int("arm_id", self.arm_id)

        safe_arm_forced: bool | None
        if self.safe_arm_forced is None:
            safe_arm_forced = None
        elif isinstance(self.safe_arm_forced, bool):
            safe_arm_forced = self.safe_arm_forced
        elif self.safe_arm_forced in (0, 1):
            safe_arm_forced = bool(self.safe_arm_forced)
        else:
            raise TypeError("safe_arm_forced must be bool-like (bool/0/1)")

        forced_reason = None
        if self.forced_reason is not None:
            forced_reason = _ensure_nonempty_str("forced_reason", self.forced_reason)

        ucb_exploitation = (
            None
            if self.ucb_exploitation is None
            else _ensure_finite("ucb_exploitation", self.ucb_exploitation)
        )
        ucb_exploration = None
        if self.ucb_exploration is not None:
            ucb_exploration = _ensure_finite("ucb_exploration", self.ucb_exploration)
        ucb_score = None if self.ucb_score is None else _ensure_finite("ucb_score", self.ucb_score)
        ucb_alpha = None if self.ucb_alpha is None else _ensure_finite("ucb_alpha", self.ucb_alpha)
        if ucb_alpha is not None and ucb_alpha <= 0.0:
            raise ValueError("ucb_alpha must be > 0")

        reward_aoi = None
        if self.reward_aoi is not None:
            reward_aoi = _ensure_finite("reward_aoi", self.reward_aoi)
        reward_mae = None
        if self.reward_mae is not None:
            reward_mae = _ensure_finite("reward_mae", self.reward_mae)
        reward_rate = None
        if self.reward_rate is not None:
            reward_rate = _ensure_finite("reward_rate", self.reward_rate)

        rate_limit_skips = (
            None
            if self.rate_limit_skips is None
            else _ensure_nonneg_int("rate_limit_skips", self.rate_limit_skips)
        )
        t_predict_ms = (
            None
            if self.t_predict_ms is None
            else _ensure_finite("t_predict_ms", self.t_predict_ms)
        )
        t_decide_ms = (
            None if self.t_decide_ms is None else _ensure_finite("t_decide_ms", self.t_decide_ms)
        )
        t_observe_ms = (
            None
            if self.t_observe_ms is None
            else _ensure_finite("t_observe_ms", self.t_observe_ms)
        )
        t_step_ms = (
            None if self.t_step_ms is None else _ensure_finite("t_step_ms", self.t_step_ms)
        )
        cpu_step_ms = (
            None
            if self.cpu_step_ms is None
            else _ensure_finite("cpu_step_ms", self.cpu_step_ms)
        )
        maxrss_kb = (
            None if self.maxrss_kb is None else _ensure_finite("maxrss_kb", self.maxrss_kb)
        )
        for name, val in (
            ("t_predict_ms", t_predict_ms),
            ("t_decide_ms", t_decide_ms),
            ("t_observe_ms", t_observe_ms),
            ("t_step_ms", t_step_ms),
            ("cpu_step_ms", cpu_step_ms),
            ("maxrss_kb", maxrss_kb),
        ):
            if val is not None and val < 0:
                raise ValueError(f"{name} must be >= 0")

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
        object.__setattr__(self, "arm_id", arm_id)
        object.__setattr__(self, "safe_arm_forced", safe_arm_forced)
        object.__setattr__(self, "forced_reason", forced_reason)
        object.__setattr__(self, "ucb_exploitation", ucb_exploitation)
        object.__setattr__(self, "ucb_exploration", ucb_exploration)
        object.__setattr__(self, "ucb_score", ucb_score)
        object.__setattr__(self, "ucb_alpha", ucb_alpha)
        object.__setattr__(self, "reward_aoi", reward_aoi)
        object.__setattr__(self, "reward_mae", reward_mae)
        object.__setattr__(self, "reward_rate", reward_rate)
        object.__setattr__(self, "rate_limit_skips", rate_limit_skips)
        object.__setattr__(self, "t_predict_ms", t_predict_ms)
        object.__setattr__(self, "t_decide_ms", t_decide_ms)
        object.__setattr__(self, "t_observe_ms", t_observe_ms)
        object.__setattr__(self, "t_step_ms", t_step_ms)
        object.__setattr__(self, "cpu_step_ms", cpu_step_ms)
        object.__setattr__(self, "maxrss_kb", maxrss_kb)

    # 직렬화/역직렬화
    def to_dict(self) -> dict[str, Any]:
        """Serialize the decision to a JSON-ready dict.

        Args:
            None.

        Returns:
            Dict with optional diagnostics omitted when None.

        Raises:
            None.

        Side Effects:
            - None.

        Contract:
            - Optional diagnostics are excluded when unset to reduce payload size.

        Failure Modes:
            - None.
        """
        d: dict[str, Any] = {
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
        if self.arm_id is not None:
            d["arm_id"] = int(self.arm_id)
        if self.safe_arm_forced is not None:
            d["safe_arm_forced"] = bool(self.safe_arm_forced)
        if self.forced_reason is not None:
            d["forced_reason"] = str(self.forced_reason)
        if self.ucb_exploitation is not None:
            d["ucb_exploitation"] = float(self.ucb_exploitation)
        if self.ucb_exploration is not None:
            d["ucb_exploration"] = float(self.ucb_exploration)
        if self.ucb_score is not None:
            d["ucb_score"] = float(self.ucb_score)
        if self.ucb_alpha is not None:
            d["ucb_alpha"] = float(self.ucb_alpha)
        if self.reward_aoi is not None:
            d["reward_aoi"] = float(self.reward_aoi)
        if self.reward_mae is not None:
            d["reward_mae"] = float(self.reward_mae)
        if self.reward_rate is not None:
            d["reward_rate"] = float(self.reward_rate)
        if self.rate_limit_skips is not None:
            d["rate_limit_skips"] = int(self.rate_limit_skips)
        if self.t_predict_ms is not None:
            d["t_predict_ms"] = float(self.t_predict_ms)
        if self.t_decide_ms is not None:
            d["t_decide_ms"] = float(self.t_decide_ms)
        if self.t_observe_ms is not None:
            d["t_observe_ms"] = float(self.t_observe_ms)
        if self.t_step_ms is not None:
            d["t_step_ms"] = float(self.t_step_ms)
        if self.cpu_step_ms is not None:
            d["cpu_step_ms"] = float(self.cpu_step_ms)
        if self.maxrss_kb is not None:
            d["maxrss_kb"] = float(self.maxrss_kb)
        return d

    def to_json_bytes(self) -> bytes:
        """Serialize the decision to compact JSON bytes.

        Args:
            None.

        Returns:
            UTF-8 encoded JSON without extra whitespace.

        Raises:
            ValueError: If serialization fails in the JSON backend.

        Side Effects:
            - None.

        Contract:
            - Uses compact JSON to minimize payload length.

        Failure Modes:
            - JSON backend errors propagate as ValueError.
        """
        return _json_dumps(self.to_dict())

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PolicyDecisionMsg:
        """Construct a PolicyDecisionMsg from a dict payload.

        Args:
            d: Parsed JSON mapping.

        Returns:
            PolicyDecisionMsg instance with validated fields.

        Raises:
            ValueError: If required keys are missing or values are out of range.
            TypeError: If field types are incompatible.

        Side Effects:
            - None.

        Contract:
            - Required keys must be present in the mapping.

        Failure Modes:
            - Raises on malformed or incomplete payloads.
        """
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
            arm_id=None if "arm_id" not in d or d["arm_id"] is None else int(d["arm_id"]),
            safe_arm_forced=(
                None
                if "safe_arm_forced" not in d or d["safe_arm_forced"] is None
                else d["safe_arm_forced"]
            ),
            forced_reason=(
                None
                if "forced_reason" not in d or d["forced_reason"] is None
                else str(d["forced_reason"])
            ),
            ucb_exploitation=(
                None
                if "ucb_exploitation" not in d or d["ucb_exploitation"] is None
                else float(d["ucb_exploitation"])
            ),
            ucb_exploration=(
                None
                if "ucb_exploration" not in d or d["ucb_exploration"] is None
                else float(d["ucb_exploration"])
            ),
            ucb_score=(
                None if "ucb_score" not in d or d["ucb_score"] is None else float(d["ucb_score"])
            ),
            ucb_alpha=(
                None if "ucb_alpha" not in d or d["ucb_alpha"] is None else float(d["ucb_alpha"])
            ),
            reward_aoi=(
                None
                if "reward_aoi" not in d or d["reward_aoi"] is None
                else float(d["reward_aoi"])
            ),
            reward_mae=(
                None
                if "reward_mae" not in d or d["reward_mae"] is None
                else float(d["reward_mae"])
            ),
            reward_rate=(
                None
                if "reward_rate" not in d or d["reward_rate"] is None
                else float(d["reward_rate"])
            ),
            rate_limit_skips=(
                None
                if "rate_limit_skips" not in d or d["rate_limit_skips"] is None
                else int(d["rate_limit_skips"])
            ),
            t_predict_ms=(
                None
                if "t_predict_ms" not in d or d["t_predict_ms"] is None
                else float(d["t_predict_ms"])
            ),
            t_decide_ms=(
                None
                if "t_decide_ms" not in d or d["t_decide_ms"] is None
                else float(d["t_decide_ms"])
            ),
            t_observe_ms=(
                None
                if "t_observe_ms" not in d or d["t_observe_ms"] is None
                else float(d["t_observe_ms"])
            ),
            t_step_ms=(
                None if "t_step_ms" not in d or d["t_step_ms"] is None else float(d["t_step_ms"])
            ),
            cpu_step_ms=(
                None
                if "cpu_step_ms" not in d or d["cpu_step_ms"] is None
                else float(d["cpu_step_ms"])
            ),
            maxrss_kb=(
                None
                if "maxrss_kb" not in d or d["maxrss_kb"] is None
                else float(d["maxrss_kb"])
            ),
        )

    @classmethod
    def from_json_bytes(cls, b: bytes) -> PolicyDecisionMsg:
        """Parse JSON bytes into a PolicyDecisionMsg instance.

        Args:
            b: UTF-8 encoded JSON bytes.

        Returns:
            Validated PolicyDecisionMsg instance.

        Raises:
            ValueError: If JSON parsing fails or payload is not an object.

        Side Effects:
            - None.

        Contract:
            - Assumes the payload follows PolicyDecisionMsg JSON schema.

        Failure Modes:
            - JSON decoding failures surface as ValueError.
        """
        try:
            d = _json_loads(b)
        except Exception as e:
            raise ValueError(f"invalid JSON: {e}") from e
        if not isinstance(d, dict):
            raise ValueError("invalid JSON: expected an object")
        return cls.from_dict(d)

    # 토픽/크기
    def mqtt_topic(self) -> str:
        """Return the MQTT topic for this policy decision.

        Args:
            None.

        Returns:
            Topic string formatted as `policy/{device_id}/decision`.

        Raises:
            None.

        Side Effects:
            - None.

        Contract:
            - `device_id` must be slash-free to preserve topic hierarchy.

        Failure Modes:
            - None.
        """
        # policy/{device_id}/decision
        return f"policy/{self.device_id}/decision"

    def estimated_mqtt_size(self, qos: int = 1) -> int:
        """Estimate MQTT v3.1.1 publish size including headers.

        Args:
            qos: MQTT QoS level used to compute header sizing.

        Returns:
            Total bytes for a PUBLISH packet at the given QoS.

        Raises:
            RuntimeError: If the MQTT size calculator is unavailable.

        Side Effects:
            - None.

        Contract:
            - Uses `mqtt_v311_publish_size` for protocol-accurate sizing.

        Failure Modes:
            - Raises if the helper is missing in minimal environments.
        """
        if mqtt_v311_publish_size is None:
            raise RuntimeError("mqtt_v311_publish_size unavailable")
        payload_len = len(self.to_json_bytes())
        return mqtt_v311_publish_size(self.mqtt_topic(), payload_len, qos=qos)
