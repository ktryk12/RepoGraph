from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PolicyContract:
    policy_id: str
    allow_enqueue: bool
    constraints: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionRequest:
    decision_id: str
    context_id: str
    task_ref: str
    truth_pack_ref: str
    truth_pack_version: int
    policy_contract: PolicyContract
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str | None = None


@dataclass(frozen=True)
class CanonicalLifecycleRequestedEvent:
    schema_version: int
    decision_id: str
    context_id: str
    status: str
    timestamp: str
    task_ref: str
    truth_pack_ref: str
    truth_pack_version: int
    metadata: dict[str, Any] = field(default_factory=dict)
