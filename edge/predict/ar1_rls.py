"""AR(1) predictor with a scalar RLS update.

This module is currently not wired into `edge.edge_daemon` (EWMA is the default predictor),
but is kept as a lightweight baseline/extension point for future experiments.

Model:
  x_t ≈ a * x_{t-1}

Scalar RLS with forgetting factor λ (0 < λ ≤ 1):
  k = P φ / (λ + φ P φ)
  a <- a + k (y - φ a)
  P <- (1/λ) (P - k φ P)

Where:
  φ = x_{t-1}, y = x_t, P is the scalar covariance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(slots=True)
class AR1RLS:
    a: float = 1.0
    lam: float = 0.99
    p: float = 1_000.0

    def __post_init__(self) -> None:
        self.a = float(self.a)
        self.lam = float(self.lam)
        self.p = float(self.p)
        if not (0.0 < self.lam <= 1.0):
            raise ValueError("lam must be in (0, 1]")
        if not (self.p > 0.0):
            raise ValueError("p must be > 0")
        if not math.isfinite(self.a):
            raise ValueError("a must be finite")

    def predict(self, x_prev: float) -> float:
        return float(self.a) * float(x_prev)

    def update(self, x: float, x_prev: float) -> float:
        y = float(x)
        phi = float(x_prev)
        if not (math.isfinite(y) and math.isfinite(phi)):
            return float(self.a)

        denom = self.lam + (phi * self.p * phi)
        if denom <= 0.0 or not math.isfinite(denom):
            return float(self.a)

        k = (self.p * phi) / denom
        err = y - (phi * self.a)
        self.a = float(self.a + k * err)
        self.p = float((self.p - k * phi * self.p) / self.lam)

        if not math.isfinite(self.a):
            self.a = 1.0
        if not (math.isfinite(self.p) and self.p > 0.0):
            self.p = 1_000.0

        return float(self.a)

