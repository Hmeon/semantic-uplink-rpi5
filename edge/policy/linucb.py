"""LinUCB 정책 스켈레톤 — (τ, k) 선택."""
from __future__ import annotations
import numpy as np
from typing import Sequence, Tuple

class LinUCB:
    """간단한 LinUCB 스켈레톤. 실제 보상/상태 스케일링은 프로젝트에 맞게 조정."""
    def __init__(self, arms: Sequence[Tuple[float, int]], d: int = 5, alpha: float = 0.7):
        self.alpha = alpha
        self.d = d
        self.arms = list(arms)
        self.A = {a: np.eye(d) for a in self.arms}
        self.b = {a: np.zeros((d, 1)) for a in self.arms}

    def select(self, s: np.ndarray) -> Tuple[float, int]:
        s = s.reshape(-1, 1)
        best, score = None, -1e18
        for a in self.arms:
            Ainv = np.linalg.inv(self.A[a])
            theta = Ainv @ self.b[a]
            p = (theta.T @ s + self.alpha * np.sqrt(s.T @ Ainv @ s)).item()
            if p > score:
                best, score = a, p
        return best

    def update(self, a: Tuple[float, int], s: np.ndarray, r: float) -> None:
        s = s.reshape(-1, 1)
        self.A[a] += s @ s.T
        self.b[a] += r * s
