"""EWMA/AR(1) 예측기 스켈레톤."""
from __future__ import annotations

class EWMA:
    def __init__(self, alpha: float = 0.9, init: float = 0.0):
        self.a = alpha
        self.y = init

    def predict(self) -> float:
        return self.y

    def update(self, x: float) -> float:
        self.y = self.a * x + (1 - self.a) * self.y
        return self.y
