"""시간/AoI/타임스탬프 유틸리티 스켈레톤."""
from __future__ import annotations
import time
from datetime import datetime, timezone

ISO_FMT = "%Y-%m-%dT%H:%M:%S.%fZ"

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime(ISO_FMT)

def iso_to_epoch(ts: str) -> float:
    # TODO: robust parser
    dt = datetime.strptime(ts, ISO_FMT).replace(tzinfo=timezone.utc)
    return dt.timestamp()

def aoi_ms(now_epoch: float, gen_epoch: float) -> float:
    return max(0.0, (now_epoch - gen_epoch) * 1000.0)
