from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseProfile(ABC):
    @property
    @abstractmethod
    def domains(self) -> list[str]:
        raise NotImplementedError

    @property
    @abstractmethod
    def agent_roster(self) -> list[str]:
        raise NotImplementedError

    @property
    @abstractmethod
    def tool_bindings(self) -> dict[str, list[str]]:
        raise NotImplementedError

    @property
    @abstractmethod
    def risk_thresholds(self) -> dict[str, float]:
        raise NotImplementedError

    @property
    @abstractmethod
    def eval_rubric(self) -> dict[str, float]:
        raise NotImplementedError

    def as_config(self) -> dict[str, Any]:
        return {
            "domains": list(self.domains),
            "agent_roster": list(self.agent_roster),
            "tool_bindings": dict(self.tool_bindings),
            "risk_thresholds": dict(self.risk_thresholds),
            "eval_rubric": dict(self.eval_rubric),
        }
