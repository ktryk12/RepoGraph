from __future__ import annotations

import time


class DepthController:
    def __init__(self, max_depth: int, timeout_budget: float, risk_level: str) -> None:
        self.max_depth = max(0, int(max_depth))
        self.timeout_budget = max(0.0, float(timeout_budget))
        self.risk_level = str(risk_level or "medium").strip().lower()
        if self.risk_level not in {"low", "medium", "high"}:
            self.risk_level = "medium"
        self._started_at = time.monotonic()

    def should_delegate(self, complexity_score: float, current_depth: int) -> bool:
        depth = max(0, int(current_depth))
        if depth >= self.max_depth:
            return False
        if self.remaining_budget() <= 0.0:
            return False

        complexity = max(0.0, min(1.0, float(complexity_score)))
        threshold = {"low": 0.75, "medium": 0.60, "high": 0.45}[self.risk_level]
        return complexity >= threshold

    def remaining_budget(self) -> float:
        elapsed = max(0.0, float(time.monotonic() - self._started_at))
        return max(0.0, float(self.timeout_budget - elapsed))
