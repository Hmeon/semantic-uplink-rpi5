"""상태 스냅샷/집계 유틸리티 (LCD/OLED 표시용)."""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque

from common.schema import LinkProfile, PolicyMode, SensorType

__all__ = ["UISnapshot", "StatusTracker"]


@dataclass(slots=True)
class UISnapshot:
    """LCD/OLED에 표시할 요약 값."""
    profile: LinkProfile
    mode: PolicyMode
    temp_c: float | None
    temp_valid: bool
    mic_dbfs: float | None
    mic_clip: float | None
    mqtt_connected: bool
    outbox_pending: int
    tx_rate_bps: float
    aoi_ms: float | None
    mae: float | None
    last_update_ns: int


class _ThroughputCounter:
    """고정 윈도우 내 전송률(bps) 추정."""
    def __init__(self, window_s: float = 10.0):
        self.window_ns = int(max(0.5, window_s) * 1e9)
        self.samples: Deque[tuple[int, int]] = deque()
        self.total_bits = 0

    def add(self, ts_ns: int, payload_len: int) -> None:
        bits = max(0, int(payload_len) * 8)
        self.samples.append((int(ts_ns), bits))
        self.total_bits += bits
        self._gc(ts_ns)

    def rate_bps(self, now_ns: int) -> float:
        self._gc(now_ns)
        if not self.samples:
            return 0.0
        span_s = max(1e-9, (now_ns - self.samples[0][0]) / 1e9)
        return float(self.total_bits) / span_s

    def _gc(self, now_ns: int) -> None:
        cutoff = int(now_ns) - self.window_ns
        while self.samples and self.samples[0][0] < cutoff:
            _, bits = self.samples.popleft()
            self.total_bits -= bits


class StatusTracker:
    """
    센서/퍼블리셔 통계 집계기.
    - T/M 센서 loop에서 호출 → 최신 값 유지
    - outbox enqueue 시 payload 길이로 전송률 추정
    """
    def __init__(self, profile: LinkProfile, mode: PolicyMode, rate_window_s: float = 10.0):
        self.profile = profile
        self.mode = mode
        self._temp_c: float | None = None
        self._temp_valid = False
        self._mic_dbfs: float | None = None
        self._mic_clip: float | None = None
        self._rate = _ThroughputCounter(window_s=rate_window_s)
        self._aoi_ms: float | None = None
        self._mae: float | None = None
        self._lock = threading.Lock()

    def update_temp(self, celsius: float, valid: bool) -> None:
        with self._lock:
            self._temp_c = float(celsius)
            self._temp_valid = bool(valid)

    def update_mic(self, dbfs: float, clip_ratio: float) -> None:
        with self._lock:
            self._mic_dbfs = float(dbfs)
            self._mic_clip = float(clip_ratio)

    def update_policy(
        self, profile: LinkProfile | None = None, mode: PolicyMode | None = None
    ) -> None:
        with self._lock:
            if profile is not None:
                self.profile = profile
            if mode is not None:
                self.mode = mode

    def record_payload(self, payload_len: int, ts_ns: int | None = None) -> None:
        ts = time.time_ns() if ts_ns is None else int(ts_ns)
        with self._lock:
            self._rate.add(ts, int(payload_len))

    def record_metrics(
        self,
        sensor: SensorType,
        aoi_ms: float,
        mae: float,
        rate_bps: float,
    ) -> None:
        # 센서 구분 없이 최근 값만 유지(표시용)
        with self._lock:
            self._aoi_ms = float(aoi_ms)
            self._mae = float(mae)
            # rate_bps는 _rate에서 집계되므로 여기서는 무시
            _ = sensor
            _ = rate_bps

    def snapshot(self, *, mqtt_connected: bool, outbox_pending: int) -> UISnapshot:
        now_ns = time.time_ns()
        with self._lock:
            rate = self._rate.rate_bps(now_ns)
            temp_c = self._temp_c
            temp_valid = self._temp_valid
            mic_dbfs = self._mic_dbfs
            mic_clip = self._mic_clip
            mode = self.mode
            profile = self.profile
            aoi_ms = self._aoi_ms
            mae = self._mae
        return UISnapshot(
            profile=profile,
            mode=mode,
            temp_c=temp_c,
            temp_valid=temp_valid,
            mic_dbfs=mic_dbfs,
            mic_clip=mic_clip,
            mqtt_connected=bool(mqtt_connected),
            outbox_pending=int(outbox_pending),
            tx_rate_bps=rate,
            aoi_ms=aoi_ms,
            mae=mae,
            last_update_ns=now_ns,
        )
