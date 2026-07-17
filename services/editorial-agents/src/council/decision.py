from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class Decision:
    id: str = field(default_factory=lambda: str(uuid4()))
    proposal_id: str = ""
    recommendation: str = "reject"
    rationale: str = ""
    confidence: float = 0.0
    risks: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    audit_trail: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
