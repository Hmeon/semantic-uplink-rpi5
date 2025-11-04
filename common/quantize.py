"""균일 양자화/복원 스켈레톤."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class UniformQuantizer:
    """k비트 균일 양자화 (스케일 구간 [xmin, xmax])."""
    kbits: int
    xmin: float
    xmax: float

    def quantize(self, x: float) -> int:
        """x를 k-bit 코드로 양자화 (TODO: 범위 클램프/라운딩)."""
        if self.kbits <= 0:
            raise ValueError("kbits must be > 0")
        # TODO: 실제 구현
        return 0

    def dequantize(self, q: int) -> float:
        """코드 q를 실수값으로 복원."""
        # TODO: 실제 구현
        return 0.0
