# common/metrics.py
# Python 3.10+
# 목적:
# - AoI(평균/분위수) 연속시간 정의 기반 계산
# - MQTT v3.1.1 PUBLISH 총 바이트(헤더 포함) 추정
# - 온라인 평균/분산(Welford), 간단 레이트/개선율 계산
# - 표준 라이브러리 중심, pandas 의존 없음

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

# 선택적 의존: 공통 모듈(존재 시 사용, 없으면 폴백 로직 사용)
try:  # EventMsg는 타입 힌트/편의용, 런타임 순환의존 방지
    from .schema import EventMsg  # type: ignore
except Exception:  # pragma: no cover
    EventMsg = None  # type: ignore

try:
    from .mqttutil import mqtt_v311_publish_size  # type: ignore
except Exception:  # pragma: no cover
    mqtt_v311_publish_size = None  # type: ignore

__all__ = [
    # AoI
    "aoi_mean_and_p95", "aoi_mean", "aoi_percentile", "AoIAggregator",
    # MQTT size / rate
    "mqtt_publish_size", "mqtt_bytes_of_event", "bytes_per_sec",
    # 통계/유틸
    "OnlineVar", "percent_improvement",
]

_NS_PER_S = 1_000_000_000
_NS_PER_MS = 1_000_000


# ---------------------------------------------------------------------------
# AoI (Age of Information) - 연속시간 정확식
# ---------------------------------------------------------------------------

def _aoi_deltas_ms(ts_ns: Sequence[int]) -> List[float]:
    """
    오름차순 타임스탬프 배열로부터 Δ_i(ms) 목록을 계산한다.
    입력이 정렬되지 않았다면 정렬 후 계산.
    """
    n = len(ts_ns)
    if n < 2:
        return []
    # 정렬 보장
    if any(ts_ns[i] > ts_ns[i + 1] for i in range(n - 1)):
        ts = sorted(int(x) for x in ts_ns)
    else:
        ts = [int(x) for x in ts_ns]
    return [(ts[i + 1] - ts[i]) / _NS_PER_MS for i in range(n - 1)]


def aoi_mean(ts_ns: Sequence[int]) -> float:
    """
    평균 AoI(ms) = Σ Δ_i^2 / (2 Σ Δ_i)
    이벤트 간격 Δ_i(ms)를 이용한 연속시간 정의의 폐형식.
    """
    deltas = _aoi_deltas_ms(ts_ns)
    if not deltas:
        return float("nan")
    s1 = sum(deltas)
    if s1 <= 0:
        return float("nan")
    s2 = sum(d * d for d in deltas)
    return float(s2 / (2.0 * s1))


def aoi_percentile(ts_ns: Sequence[int], p: float = 0.95) -> float:
    """
    AoI의 p 분위수(ms). 길이-가중 균일 혼합의 분위수 a*는
    Σ min(a*, Δ_i) = p · Σ Δ_i 를 만족하는 해.
    """
    if not (0.0 < p < 1.0):
        raise ValueError("p must be in (0,1)")
    deltas = _aoi_deltas_ms(ts_ns)
    if not deltas:
        return float("nan")
    total = float(sum(deltas))
    if total <= 0:
        return float("nan")
    target = p * total
    d_sorted = sorted(deltas)
    n = len(d_sorted)
    # 꼬리합: 각 인덱스부터 끝까지의 합
    tail = [0.0] * n
    acc = 0.0
    for i in range(n - 1, -1, -1):
        acc += d_sorted[i]
        tail[i] = acc
    # i개 구간에서 a < Δ_i 가정하면 sum(min(a,Δ)) = a*i + sum_{j>i} Δ_j
    for i in range(1, n + 1):
        sum_tail = tail[n - i] if (n - i) < n else 0.0
        a = (target - sum_tail) / i
        # a는 [d_sorted[i-1], d_sorted[i]] 사이에서만 유효
        if i == n or a <= d_sorted[i]:
            return float(max(0.0, a))
    # 폴백
    return float(d_sorted[-1])


def aoi_mean_and_p95(ts_ns: Sequence[int]) -> Tuple[float, float]:
    """편의 함수: 평균·P95(ms) 동시 계산."""
    return aoi_mean(ts_ns), aoi_percentile(ts_ns, 0.95)


@dataclass(slots=True)
class AoIAggregator:
    """
    스트림 처리용 AoI 누적기.
    - push(ts_ns)를 순서대로 호출(미정렬 입력이라면 자체 정렬 대신 누적 Δ로 계산 권장)
    - 메모리 효율: 평균은 ΣΔ, ΣΔ²로 즉시 계산, 분위수는 Δ 벡터를 보관하여 post-hoc 계산
    """
    _last_ts_ns: int | None = None
    _sum_ms: float = 0.0
    _sum_sq_ms2: float = 0.0
    _deltas_ms: List[float] | None = None  # 분위수 계산용(옵션 저장)
    _n: int = 0

    def __init__(self, keep_deltas: bool = True):
        object.__setattr__(self, "_last_ts_ns", None)
        object.__setattr__(self, "_sum_ms", 0.0)
        object.__setattr__(self, "_sum_sq_ms2", 0.0)
        object.__setattr__(self, "_deltas_ms", [] if keep_deltas else None)
        object.__setattr__(self, "_n", 0)

    def push(self, ts_ns: int) -> None:
        ts_ns = int(ts_ns)
        last = self._last_ts_ns
        object.__setattr__(self, "_last_ts_ns", ts_ns)
        if last is None:
            return
        d_ms = (ts_ns - last) / _NS_PER_MS
        if d_ms < 0:
            # 역전 시간은 무시(로그 오염 방지). 필요 시 사전 정렬하여 공급.
            return
        object.__setattr__(self, "_sum_ms", self._sum_ms + d_ms)
        object.__setattr__(self, "_sum_sq_ms2", self._sum_sq_ms2 + d_ms * d_ms)
        object.__setattr__(self, "_n", self._n + 1)
        if self._deltas_ms is not None:
            self._deltas_ms.append(d_ms)

    def mean_ms(self) -> float:
        if self._sum_ms <= 0:
            return float("nan")
        return float(self._sum_sq_ms2 / (2.0 * self._sum_ms))

    def p_ms(self, p: float = 0.95) -> float:
        if self._deltas_ms is None or not self._deltas_ms:
            return float("nan")
        return aoi_percentile_from_deltas(self._deltas_ms, p)

    def finalize(self, p: float = 0.95) -> Tuple[float, float]:
        """(mean_ms, p_ms(p))"""
        return self.mean_ms(), self.p_ms(p)


def aoi_percentile_from_deltas(deltas_ms: Sequence[float], p: float = 0.95) -> float:
    """Δ 목록에서 직접 AoI 분위수를 계산(내부/집계기 공용)."""
    if not (0.0 < p < 1.0):
        raise ValueError("p must be in (0,1)")
    if not deltas_ms:
        return float("nan")
    total = float(sum(deltas_ms))
    if total <= 0:
        return float("nan")
    target = p * total
    d_sorted = sorted(float(x) for x in deltas_ms)
    n = len(d_sorted)
    tail = [0.0] * n
    acc = 0.0
    for i in range(n - 1, -1, -1):
        acc += d_sorted[i]
        tail[i] = acc
    for i in range(1, n + 1):
        sum_tail = tail[n - i] if (n - i) < n else 0.0
        a = (target - sum_tail) / i
        if i == n or a <= d_sorted[i]:
            return float(max(0.0, a))
    return float(d_sorted[-1])


# ---------------------------------------------------------------------------
# MQTT v3.1.1 PUBLISH 총 바이트(헤더 포함) 추정
# ---------------------------------------------------------------------------

def mqtt_publish_size(topic: str, payload_len: int, qos: int = 1) -> int:
    """
    MQTT v3.1.1 PUBLISH 패킷 총 크기(바이트)를 계산.
    - 가능한 경우 common.mqttutil의 참조 구현을 사용.
    - 폴백 경로: 표준에 따른 Remaining Length 인코딩/가변 헤더 길이를 직접 계산.
    """
    if mqtt_v311_publish_size is not None:
        return int(mqtt_v311_publish_size(topic, int(payload_len), qos=int(qos)))
    # --- 폴백 구현 ---
    if not isinstance(topic, str) or not topic:
        raise ValueError("topic must be non-empty str")
    if qos not in (0, 1, 2):
        raise ValueError("qos must be 0, 1, or 2")
    plen = int(payload_len)
    if plen < 0:
        raise ValueError("payload_len must be >=0")

    # 가변 헤더 길이: topic length(2) + topic + (qos>0이면 packet id 2)
    vh_len = 2 + len(topic.encode("utf-8")) + (2 if qos > 0 else 0)
    # 고정 헤더: 1바이트 타입/플래그 + Remaining Length(가변)
    remaining = vh_len + plen
    rl_len = _mqtt_remaining_length_nbytes(remaining)
    # 총합
    return 1 + rl_len + remaining


def _mqtt_remaining_length_nbytes(remaining: int) -> int:
    """MQTT Remaining Length 가변 인코딩에 필요한 바이트 수."""
    if remaining < 0:
        raise ValueError("remaining must be >= 0")
    # 1바이트에 7비트씩 사용
    n = 1
    while remaining > 127:
        remaining //= 128
        n += 1
    return n


def mqtt_bytes_of_event(event: "EventMsg", qos: int = 1) -> int:
    """
    EventMsg 한 건의 MQTT PUBLISH 총 바이트 수(헤더 포함)를 계산.
    (토픽은 스키마 규칙 'edge/{device}/{sensor}/event' 사용)
    """
    if EventMsg is None:
        raise RuntimeError("EventMsg not available")
    topic = event.mqtt_topic()
    payload_len = len(event.to_json_bytes())
    return mqtt_publish_size(topic, payload_len, qos=qos)


# ---------------------------------------------------------------------------
# 레이트/개선율/온라인 분산
# ---------------------------------------------------------------------------

def bytes_per_sec(total_bytes: int, start_ns: int, end_ns: int) -> float:
    """
    구간 레이트(B/s) = total_bytes / duration[s]
    """
    dur_ns = int(end_ns) - int(start_ns)
    if dur_ns <= 0:
        return float("nan")
    return float(int(total_bytes) / (dur_ns / _NS_PER_S))


def percent_improvement(baseline: float, candidate: float) -> float:
    """
    개선율(%) = (baseline - candidate) / baseline * 100
    baseline<=0이면 NaN.
    """
    b = float(baseline); c = float(candidate)
    if not math.isfinite(b) or b <= 0:
        return float("nan")
    return (b - c) / b * 100.0


@dataclass(slots=True)
class OnlineVar:
    """
    온라인 평균/분산(Welford).
    - update(x) 호출로 스트림에서 즉시 평균/분산 추정
    - 분산은 표본분산(unbiased, n-1) 반환
    """
    _n: int = 0
    _mean: float = 0.0
    _m2: float = 0.0

    def update(self, x: float) -> None:
        x = float(x)
        n1 = self._n
        n = n1 + 1
        delta = x - self._mean if n1 > 0 else x - 0.0
        mean = self._mean + delta / n
        delta2 = x - mean
        m2 = self._m2 + delta * delta2
        object.__setattr__(self, "_n", n)
        object.__setattr__(self, "_mean", mean)
        object.__setattr__(self, "_m2", m2)

    def reset(self) -> None:
        object.__setattr__(self, "_n", 0)
        object.__setattr__(self, "_mean", 0.0)
        object.__setattr__(self, "_m2", 0.0)

    @property
    def count(self) -> int:
        return self._n

    @property
    def mean(self) -> float:
        return float(self._mean) if self._n > 0 else float("nan")

    @property
    def var(self) -> float:
        if self._n < 2:
            return float("nan")
        return float(self._m2 / (self._n - 1))

    @property
    def std(self) -> float:
        v = self.var
        return float(math.sqrt(v)) if math.isfinite(v) else float("nan")
