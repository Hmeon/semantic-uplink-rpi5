"""Validated configuration schemas and YAML loaders.

Defines pydantic models for policy, device, and link shaping configs, plus
helpers to load YAML files into those schemas. Extra fields are rejected to
keep configs strict and compatible with experiment tooling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class ArmConfig(BaseModel):
    """Policy arm definition for (tau, kbits).

    Args:
        tau: Threshold/interval for the arm (must be > 0).
        kbits: Quantization bit width (1..16).

    Returns:
        None.

    Raises:
        ValueError: If tau <= 0 or kbits out of range.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Validation errors surface during config parsing.
    """
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
    """Reward weight configuration for policy training.

    Args:
        alpha: AoI weight.
        beta: MAE weight.
        gamma: Rate weight.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Invalid types surface as validation errors.
    """
    model_config = ConfigDict(extra="forbid")

    alpha: float = 1.0
    beta: float = 1.0
    gamma: float = 1.0


class ScaleConfig(BaseModel):
    """Normalization scales for policy state and reward.

    Args:
        aoi_ms: AoI scale in milliseconds.
        rate_bps: Rate scale in bytes per second.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Invalid types surface as validation errors.
    """
    model_config = ConfigDict(extra="forbid")

    aoi_ms: float = 1000.0
    rate_bps: float = 1024.0


class SafetyConfig(BaseModel):
    """Safety thresholds for policy override behavior.

    Args:
        aoi_max_ms: AoI limit in milliseconds.
        mae_max: MAE limit in sensor units.
        safety_force_emit_on_aoi: Force emission when AoI exceeds limit.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Invalid types surface as validation errors.
    """
    model_config = ConfigDict(extra="forbid")

    aoi_max_ms: float = 5_000.0
    mae_max: float = 2.0
    safety_force_emit_on_aoi: bool = False


class PolicyDiagnosticsConfig(BaseModel):
    """Toggle policy diagnostics emission.

    Args:
        enabled: Enable diagnostic metrics when True.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Invalid types surface as validation errors.
    """
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class SensorPolicyConfig(BaseModel):
    """Optional per-sensor policy overrides.

    Args:
        arms: Optional arm list overriding the global arms.
        reward: Optional reward weight overrides.
        safety: Optional safety threshold overrides.
        diagnostics: Optional diagnostics toggle overrides.
        scales: Optional normalization scale overrides.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Invalid nested configs surface as validation errors.
    """
    model_config = ConfigDict(extra="forbid")

    arms: list[ArmConfig] | None = None
    reward: RewardConfig | None = None
    safety: SafetyConfig | None = None
    diagnostics: PolicyDiagnosticsConfig | None = None
    scales: ScaleConfig | None = None


class PolicyConfig(BaseModel):
    """Top-level policy configuration for adaptive mode.

    Args:
        arms: Arm grid for the policy.
        reward: Reward weight configuration.
        safety: Safety threshold configuration.
        diagnostics: Diagnostic emission configuration.
        scales: Normalization scale configuration.
        sensors: Optional per-sensor overrides keyed by sensor name.

    Returns:
        None.

    Raises:
        ValueError: If the arm list is empty.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.
        - At least one arm must be provided.

    Failure Modes:
        - Validation errors surface during config parsing.
    """
    model_config = ConfigDict(extra="forbid")

    arms: list[ArmConfig]
    reward: RewardConfig = Field(default_factory=RewardConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    diagnostics: PolicyDiagnosticsConfig = Field(default_factory=PolicyDiagnosticsConfig)
    scales: ScaleConfig = Field(default_factory=ScaleConfig)
    sensors: dict[str, SensorPolicyConfig] = Field(default_factory=dict)

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
    """Load and validate a policy config from YAML.

    Args:
        path: Path to a policy YAML file.

    Returns:
        Validated PolicyConfig instance.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If YAML parsing or validation fails.
        TypeError: If the YAML root is not a mapping.

    Side Effects:
        - Reads the YAML file from disk.

    Contract:
        - Extra fields are rejected by the schema.

    Failure Modes:
        - Validation errors are surfaced as ValueError with context.
    """
    p = Path(path)
    try:
        return PolicyConfig.model_validate(_load_yaml_mapping(p))
    except ValidationError as exc:
        raise ValueError(f"invalid policy config: {p}\n{exc}") from exc


def load_policy_config_dict(path: str | Path) -> dict[str, Any]:
    """Load a policy config and return it as a plain dict.

    Args:
        path: Path to a policy YAML file.

    Returns:
        Dict representation of PolicyConfig.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If YAML parsing or validation fails.
        TypeError: If the YAML root is not a mapping.

    Side Effects:
        - Reads the YAML file from disk.

    Contract:
        - Uses the same schema validation as load_policy_config.

    Failure Modes:
        - Validation errors are surfaced as ValueError with context.
    """
    return load_policy_config(path).model_dump()


class DeviceMicConfig(BaseModel):
    """Device mic sampling configuration.

    Args:
        frame_ms: Frame length in milliseconds.
        samplerate: Sampling rate in Hz.
        normalize: Normalize input samples when True.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Invalid types surface as validation errors.
    """
    model_config = ConfigDict(extra="forbid")

    frame_ms: int = 100
    samplerate: int = 16_000
    normalize: bool = True


class DeviceTempConfig(BaseModel):
    """Device temperature sampling configuration.

    Args:
        period_hz: Sampling frequency in Hz.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Invalid types surface as validation errors.
    """
    model_config = ConfigDict(extra="forbid")

    period_hz: float = 1.0


class DeviceSensorsConfig(BaseModel):
    """Container for enabled sensor configurations.

    Args:
        mic: Optional mic configuration.
        temp: Optional temperature configuration.

    Returns:
        None.

    Raises:
        ValueError: If both mic and temp are omitted.

    Side Effects:
        - None.

    Contract:
        - At least one sensor must be configured.

    Failure Modes:
        - Validation errors surface during config parsing.
    """
    model_config = ConfigDict(extra="forbid")

    mic: DeviceMicConfig | None = None
    temp: DeviceTempConfig | None = None

    @model_validator(mode="after")
    def _at_least_one_sensor(self) -> DeviceSensorsConfig:
        if self.mic is None and self.temp is None:
            raise ValueError("at least one of sensors.mic or sensors.temp must be set")
        return self


class DeviceUIConfig(BaseModel):
    """Device UI configuration for status displays.

    Args:
        enabled: Enable UI when True.
        backend: UI backend name (e.g., console, lcd).

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Invalid types surface as validation errors.
    """
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    backend: str = "console"


class DeviceButtonsConfig(BaseModel):
    """Device GPIO buttons configuration.

    Args:
        enabled: Enable GPIO buttons when True.
        mode_pin: GPIO pin for mode button.
        profile_pin: GPIO pin for profile button.
        marker_pin: GPIO pin for marker button.
        debounce_ms: Debounce interval in milliseconds.

    Returns:
        None.

    Raises:
        ValueError: If pin numbers are non-positive or debounce is negative.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Validation errors surface during config parsing.
    """
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    mode_pin: int = 17
    profile_pin: int = 27
    marker_pin: int = 22
    debounce_ms: int = 200

    @field_validator("mode_pin", "profile_pin", "marker_pin")
    @classmethod
    def _pin_positive(cls, v: int) -> int:
        iv = int(v)
        if iv <= 0:
            raise ValueError("pin must be > 0")
        return iv

    @field_validator("debounce_ms")
    @classmethod
    def _debounce_nonneg(cls, v: int) -> int:
        iv = int(v)
        if iv < 0:
            raise ValueError("debounce_ms must be >= 0")
        return iv


class DeviceMQTTConfig(BaseModel):
    """Device MQTT connection settings.

    Args:
        host: Broker hostname or IP.
        port: Broker port.
        base_topic: Base topic prefix for publishing.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Invalid types surface as validation errors.
    """
    model_config = ConfigDict(extra="forbid")

    host: str
    port: int = 1883
    base_topic: str = "edge"


class DeviceConfig(BaseModel):
    """Top-level device configuration schema.

    Args:
        device_id: Device identifier used in topics and logs.
        sensors: Sensor configuration block.
        ui: Optional UI configuration.
        buttons: Optional GPIO buttons configuration.
        mqtt: MQTT connection configuration.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Validation errors surface during config parsing.
    """
    model_config = ConfigDict(extra="forbid")

    device_id: str
    sensors: DeviceSensorsConfig
    ui: DeviceUIConfig | None = None
    buttons: DeviceButtonsConfig | None = None
    mqtt: DeviceMQTTConfig


def load_device_config(path: str | Path) -> DeviceConfig:
    """Load and validate a device config from YAML.

    Args:
        path: Path to a device YAML file.

    Returns:
        Validated DeviceConfig instance.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If YAML parsing or validation fails.
        TypeError: If the YAML root is not a mapping.

    Side Effects:
        - Reads the YAML file from disk.

    Contract:
        - Extra fields are rejected by the schema.

    Failure Modes:
        - Validation errors are surfaced as ValueError with context.
    """
    p = Path(path)
    try:
        return DeviceConfig.model_validate(_load_yaml_mapping(p))
    except ValidationError as exc:
        raise ValueError(f"invalid device config: {p}\n{exc}") from exc


class LinkProfileConfig(BaseModel):
    """Link shaping profile configuration.

    Args:
        rate_kbit: Fixed egress rate in kbit; None enables variable mode.
        delay_ms: Base delay in milliseconds.
        jitter_ms: Additional jitter in milliseconds.
        loss_pct: Loss percentage (0..100).
        loss_corr_pct: Loss correlation percentage (0..100).
        reorder_pct: Reorder percentage (0..100).
        low_kbit: Low rate for variable mode.
        high_kbit: High rate for variable mode.
        var_default_period_s: Toggle period for variable mode in seconds.

    Returns:
        None.

    Raises:
        ValueError: If rate/jitter/loss bounds are invalid.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.
        - Variable mode requires low_kbit and high_kbit.

    Failure Modes:
        - Validation errors surface during config parsing.
    """
    model_config = ConfigDict(extra="forbid")

    # Structured profile (preferred). This matches `link/shaper/tc_profiles.py::TcProfile`.
    rate_kbit: int | None = None
    delay_ms: int = 0
    jitter_ms: int = 0
    loss_pct: float = 0.0
    loss_corr_pct: float = 0.0
    reorder_pct: float = 0.0

    # cellular_var-like (rate_kbit is None) toggling parameters.
    low_kbit: int | None = None
    high_kbit: int | None = None
    var_default_period_s: int = 30

    @model_validator(mode="after")
    def _validate(self) -> LinkProfileConfig:
        if self.rate_kbit is None:
            if self.low_kbit is None or self.high_kbit is None:
                raise ValueError("when rate_kbit is null, low_kbit and high_kbit are required")
            if int(self.low_kbit) <= 0 or int(self.high_kbit) <= 0:
                raise ValueError("low_kbit/high_kbit must be > 0")
        else:
            if int(self.rate_kbit) <= 0:
                raise ValueError("rate_kbit must be > 0")

        if int(self.delay_ms) < 0 or int(self.jitter_ms) < 0:
            raise ValueError("delay_ms/jitter_ms must be >= 0")
        for name, v in [
            ("loss_pct", self.loss_pct),
            ("loss_corr_pct", self.loss_corr_pct),
            ("reorder_pct", self.reorder_pct),
        ]:
            fv = float(v)
            if fv < 0.0 or fv > 100.0:
                raise ValueError(f"{name} must be in [0, 100]")

        if int(self.var_default_period_s) < 2:
            raise ValueError("var_default_period_s must be >= 2")
        return self


class LinkProfilesConfig(BaseModel):
    """Collection of named link profiles.

    Args:
        profiles: Mapping of profile name to LinkProfileConfig.

    Returns:
        None.

    Raises:
        None.

    Side Effects:
        - None.

    Contract:
        - Extra fields are forbidden.

    Failure Modes:
        - Validation errors surface during config parsing.
    """
    model_config = ConfigDict(extra="forbid")

    profiles: dict[str, LinkProfileConfig]


def load_link_profiles_config(path: str | Path) -> LinkProfilesConfig:
    """Load and validate link profile configs from YAML.

    Args:
        path: Path to a link profiles YAML file.

    Returns:
        Validated LinkProfilesConfig instance.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If YAML parsing or validation fails.
        TypeError: If the YAML root is not a mapping.

    Side Effects:
        - Reads the YAML file from disk.

    Contract:
        - Extra fields are rejected by the schema.

    Failure Modes:
        - Validation errors are surfaced as ValueError with context.
    """
    p = Path(path)
    try:
        return LinkProfilesConfig.model_validate(_load_yaml_mapping(p))
    except ValidationError as exc:
        raise ValueError(f"invalid link profiles config: {p}\n{exc}") from exc
