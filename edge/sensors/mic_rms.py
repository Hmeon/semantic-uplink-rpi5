"""마이크 RMS→dBFS 센서 어댑터 스켈레톤."""
from __future__ import annotations
from typing import Iterator, Tuple

def stream_frames(device: int | None = None) -> Iterator[Tuple[float, float]]:
    """100ms 프레임 단위로 (epoch_ts, dBFS) 를 생성.
    TODO: sounddevice로 구현. 무음/캘리브레이션 처리.
    """
    raise NotImplementedError
