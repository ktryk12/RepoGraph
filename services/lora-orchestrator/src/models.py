from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class GapReport:
    gap_id: str
    domain: str
    severity: Literal["low", "medium", "high"]
    evidence: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AdapterCandidate:
    candidate_id: str
    source_url: str
    license: str
    base_model: str
    param_count: int
    last_updated: datetime
    file_path: Path
    file_format: Literal["safetensors", "pickle", "other"]


@dataclass(frozen=True)
class SecurityScore:
    candidate_id: str
    s6_passed: bool
    s7_passed: bool
    s8_passed: bool
    overall_score: float
    disqualification_reason: str | None = None


@dataclass(frozen=True)
class LoRAFlowResult:
    gap_id: str
    outcome: Literal["external_adapter", "self_trained", "deferred"]
    adapter_id: str | None
    security_score: float
    votes: dict[str, bool]
    warnings: list[str]
    next_evaluation: datetime
