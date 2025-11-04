"""AR(1) + RLS(선택) 예측기 스켈레톤."""
from __future__ import annotations

class AR1RLS:
    def __init__(self, lam: float = 0.99):
        self.a = 1.0  # AR(1) 계수 (초깃값)
        self.lam = lam
        # TODO: 공분산/게인 초기화

    def predict(self, x_prev: float) -> float:
        return self.a * x_prev

    def update(self, x: float, x_prev: float) -> float:
        """RLS로 a 업데이트 (TODO)."""
        return self.a
