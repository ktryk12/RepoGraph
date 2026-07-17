from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


class GovernanceViolationError(Exception):
    pass


class PolicyAction(BaseModel):
    action_id: str = Field(default_factory=lambda: str(uuid4()))
    type: str
    auto_approved: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    executed_at: datetime | None = None
    status: str = "pending"
    reason: str = ""


class ThresholdAction(PolicyAction):
    type: Literal["threshold"] = "threshold"
    target_layer: int
    parameter: str
    current_value: float
    new_value: float

    @model_validator(mode="after")
    def _validate_change_ratio(self) -> "ThresholdAction":
        baseline = abs(float(self.current_value))
        if baseline <= 1e-12:
            return self
        delta = abs(float(self.new_value) - float(self.current_value))
        change_ratio = delta / baseline
        if change_ratio > (0.30 + 1e-9):
            raise GovernanceViolationError("threshold_change_ratio_exceeds_30pct")
        return self


class SandboxAction(PolicyAction):
    type: Literal["sandbox"] = "sandbox"
    skill_id: str
    sandbox_hours: int = 24


class NormalizationAction(PolicyAction):
    type: Literal["normalization"] = "normalization"
    new_pattern: str


class Advisory(PolicyAction):
    type: Literal["advisory"] = "advisory"
    action_payload: dict[str, Any] = Field(default_factory=dict)
    threat_severity: float = 0.0
