from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class ArmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tau: float
    kbits: int

    @field_validator("tau")
    @classmethod
    def _tau_positive(cls, v: float) -> float:
        fv = float(v)
        if fv <= 0:
            raise ValueError("tau must be > 0")
        return fv

    @field_validator("kbits")
    @classmethod
    def _kbits_range(cls, v: int) -> int:
        iv = int(v)
        if not (1 <= iv <= 16):
            raise ValueError("kbits must be in [1, 16]")
        return iv


class RewardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alpha: float = 1.0
    beta: float = 1.0
    gamma: float = 1.0


class SafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aoi_max_ms: float = 5_000.0
    mae_max: float = 2.0


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arms: list[ArmConfig]
    reward: RewardConfig = Field(default_factory=RewardConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)

    @field_validator("arms")
    @classmethod
    def _arms_nonempty(cls, v: list[ArmConfig]) -> list[ArmConfig]:
        if not v:
            raise ValueError("arms must not be empty")
        return v


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"config not found: {path}") from exc
    except Exception as exc:
        raise ValueError(f"failed to parse YAML: {path}. error={exc}") from exc

    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise TypeError(f"config must be a mapping (YAML dict): {path}")
    return raw


def load_policy_config(path: str | Path) -> PolicyConfig:
    p = Path(path)
    try:
        return PolicyConfig.model_validate(_load_yaml_mapping(p))
    except ValidationError as exc:
        raise ValueError(f"invalid policy config: {p}\n{exc}") from exc


def load_policy_config_dict(path: str | Path) -> dict[str, Any]:
    return load_policy_config(path).model_dump()


class DeviceMicConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    frame_ms: int = 100
    samplerate: int = 16_000
    normalize: bool = True


class DeviceTempConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_hz: float = 1.0


class DeviceSensorsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mic: DeviceMicConfig | None = None
    temp: DeviceTempConfig | None = None

    @model_validator(mode="after")
    def _at_least_one_sensor(self) -> DeviceSensorsConfig:
        if self.mic is None and self.temp is None:
            raise ValueError("at least one of sensors.mic or sensors.temp must be set")
        return self


class DeviceUIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    backend: str = "console"


class DeviceMQTTConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    port: int = 1883
    base_topic: str = "edge"


class DeviceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str
    sensors: DeviceSensorsConfig
    ui: DeviceUIConfig | None = None
    mqtt: DeviceMQTTConfig


def load_device_config(path: str | Path) -> DeviceConfig:
    p = Path(path)
    try:
        return DeviceConfig.model_validate(_load_yaml_mapping(p))
    except ValidationError as exc:
        raise ValueError(f"invalid device config: {p}\n{exc}") from exc


class LinkProfileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tbf: str
    netem: str


class LinkProfilesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profiles: dict[str, LinkProfileConfig]


def load_link_profiles_config(path: str | Path) -> LinkProfilesConfig:
    p = Path(path)
    try:
        return LinkProfilesConfig.model_validate(_load_yaml_mapping(p))
    except ValidationError as exc:
        raise ValueError(f"invalid link profiles config: {p}\n{exc}") from exc
