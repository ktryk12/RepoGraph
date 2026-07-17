from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Proposal:
    id: str = field(default_factory=lambda: str(uuid4()))
    claim: str = ""
    evidence: list[Any] = field(default_factory=list)
    confidence: float = 0.0
    assumptions: list[str] = field(default_factory=list)
    requested_decision: str = "accept_or_reject"
    submitter_role: str = "planner_agent"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
