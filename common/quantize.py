# common/quantize.py
# Python 3.10+
# 목적: 센서값을 균일(midtread) 양자화로 kbits에 맞춰 양자화/복원한다.
# - Event 스키마의 kbits와 1:1 정합(1..16bit).  [과제 제안서/동결안 준수]  # noqa
# - 센서별 기본 범위: mic_rms(dBFS)=[-80,0], temp(°C)=[0,50]  *필요 시 인자로 재정의 가능*
# - 포화(saturation) 여부를 반환해 분석/튜닝(τ, k) 시 경계효과를 추적 가능.
# - rounding은 "최근접 반올림(0.5↑)"로 고정(파이썬 기본 banker's rounding 회피).
# - dequantize는 코드→대표값(격자점) 복원(엔드포인트 포함 midtread).

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

import numpy as np

try:
    # 센서 타입 열거형(문자열 값 고정). 상대 import로 순환 의존 회피.
    from .schema import SensorType
except Exception:  # 분석/독립 실행 시 최소한의 대체
    from enum import Enum
    class SensorType(str, Enum):  # type: ignore[redefinition]
        MIC_RMS = "mic_rms"
        TEMP = "temp"

__all__ = [
    "UniformQuantizer", "QResult",
    "quantizer_for_sensor", "quantize_value", "quantize_array",
]

# -------------------- 기본 범위(프로젝트 권고값) --------------------
# 마이크 RMS(dBFS): 실내 환경 기준 -80..0 dBFS 범위에서 충분(원음은 취급하지 않음)
# 온도(°C): 실내 PoC 기준 0..50°C로 충분(DS18B20 전체범위는 -55..125°C이나 PoC 범위 축소)  :contentReference[oaicite:1]{index=1}
SANE_RANGE = {
    SensorType.MIC_RMS: (-80.0, 0.0),
    SensorType.TEMP: (0.0, 50.0),
}


def _levels(kbits: int) -> int:
    if not isinstance(kbits, int):
        raise TypeError("kbits must be int")
    if not (1 <= kbits <= 16):
        raise ValueError("kbits must be in [1, 16]")
    return 1 << kbits


def _nearest_int(x: float) -> int:
    # 최근접 반올림(0.5↑) — 파이썬 round의 bankers rounding(짝수 우선)을 회피
    return int(math.floor(x + 0.5))


@dataclass(frozen=True, slots=True)
class QResult:
    """양자화 결과(디버깅/로깅 친화)."""
    q: float          # 양자화된 실수값(대표값)
    code: int         # 0..(levels-1) 정수 코드
    saturated: bool   # 경계 포화 여부
    step: float       # 양자화 간격(Δ)


@dataclass(frozen=True, slots=True, init=False)
class UniformQuantizer:
    """
    균일(mid-tread) 양자화기. vmin..vmax 구간을 (2^kbits)개의 격자점으로 균등 분할.
    - 격자점: vmin + i*Δ (i=0..L-1), Δ = (vmax-vmin)/(L-1)
    - 엔드포인트 포함: 입력이 범위를 벗어나면 [vmin, vmax]로 포화.
    """
    vmin: float
    vmax: float
    kbits: int

    def __init__(self, vmin: float | None = None, vmax: float | None = None, kbits: int | None = None, **kwargs) -> None:
        if vmin is None and "xmin" in kwargs:
            vmin = kwargs.pop("xmin")
        if vmax is None and "xmax" in kwargs:
            vmax = kwargs.pop("xmax")
        if kbits is None and "bits" in kwargs:
            kbits = kwargs.pop("bits")
        if kwargs:
            raise TypeError(f"unexpected keyword arguments: {', '.join(sorted(kwargs))}")
        if vmin is None or vmax is None or kbits is None:
            raise TypeError("vmin, vmax and kbits are required")

        object.__setattr__(self, "vmin", float(vmin))
        object.__setattr__(self, "vmax", float(vmax))
        object.__setattr__(self, "kbits", int(kbits))
        self.__post_init__()

    def __post_init__(self):
        if not (math.isfinite(self.vmin) and math.isfinite(self.vmax)):
            raise ValueError("vmin/vmax must be finite")
        if not (self.vmax > self.vmin):
            raise ValueError("vmax must be > vmin")
        _ = _levels(self.kbits)  # 유효성 검증

    @property
    def levels(self) -> int:
        return _levels(self.kbits)

    @property
    def step(self) -> float:
        # L==2일 때도 엔드포인트 포함을 보장(Δ = range/1)
        return (self.vmax - self.vmin) / (self.levels - 1)

    def _to_code(self, x: float) -> Tuple[int, bool]:
        if not math.isfinite(x):
            raise ValueError("x must be finite")
        # 포화
        if x <= self.vmin:
            return 0, True
        if x >= self.vmax:
            return self.levels - 1, True
        # 최근접 격자점 인덱스
        idx_f = (x - self.vmin) / self.step
        code = _nearest_int(idx_f)
        # 안정성: 경계 클램핑
        if code < 0:
            code, sat = 0, True
        elif code > self.levels - 1:
            code, sat = self.levels - 1, True
        else:
            sat = False
        return int(code), sat

    def quantize(self, x: float) -> QResult:
        code, sat = self._to_code(x)
        q = self.dequantize(code)
        return QResult(q=q, code=code, saturated=sat, step=self.step)

    def dequantize(self, code: int) -> float:
        if not isinstance(code, int):
            raise TypeError("code must be int")
        if not (0 <= code <= self.levels - 1):
            raise ValueError(f"code out of range: {code} (0..{self.levels-1})")
        return self.vmin + code * self.step


# -------------------- 센서별 팩토리 & 헬퍼 --------------------

def quantizer_for_sensor(sensor: SensorType, kbits: int,
                         vmin: float | None = None, vmax: float | None = None) -> UniformQuantizer:
    """
    센서별 권고 범위를 기본으로 하되, 필요 시 vmin/vmax를 재정의할 수 있다.
    - mic_rms(dBFS): 기본 [-80, 0]
    - temp(°C): 기본 [0, 50]
    """
    if vmin is None or vmax is None:
        dflt = SANE_RANGE.get(sensor)
        if dflt is None:
            raise ValueError(f"unsupported sensor: {sensor}")
        if vmin is None:
            vmin = dflt[0]
        if vmax is None:
            vmax = dflt[1]
    return UniformQuantizer(float(vmin), float(vmax), int(kbits))


def quantize_value(sensor: SensorType, x: float, kbits: int,
                   vmin: float | None = None, vmax: float | None = None) -> float:
    """
    스칼라 값을 양자화하여 **대표값(실수)**을 반환. (Event.val에 그대로 사용)
    """
    q = quantizer_for_sensor(sensor, kbits, vmin=vmin, vmax=vmax)
    return q.quantize(float(x)).q


def quantize_array(q: UniformQuantizer, xs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """
    배열 양자화(벡터화). 반환: (qvals[float64], codes[int32])
    - 포화 여부가 중요하면, (xs< vmin)|(xs>vmax)로 별도 마스크 생성.
    """
    arr = np.asarray(xs, dtype=np.float64)
    # 포화 클램프
    clamped = np.clip(arr, q.vmin, q.vmax)
    idx_f = (clamped - q.vmin) / q.step
    codes = np.floor(idx_f + 0.5).astype(np.int64)
    np.clip(codes, 0, q.levels - 1, out=codes)
    qvals = (q.vmin + codes * q.step).astype(np.float64)
    return qvals, codes.astype(np.int32)
