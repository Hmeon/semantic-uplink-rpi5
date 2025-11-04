"""온도 센서 어댑터 스켈레톤."""
from __future__ import annotations
from typing import Iterator, Tuple

def stream_temp() -> Iterator[Tuple[float, float]]:
    """1Hz 주기로 (epoch_ts, degC) 반환.
    TODO: I2C/1-Wire 드라이버 바인딩.
    """
    raise NotImplementedError
