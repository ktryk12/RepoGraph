"""services/claim-detector/claim_detector/models.py — ClaimCandidate + DetectedClaim."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class ClaimCandidate:
    raw_text: str
    source_url: str
    platform: str
    virality_score: float = 0.0
    controversy_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DetectedClaim:
    claim_id: str
    raw_text: str
    source_url: str
    platform: str
    detected_at: str
    virality_score: float
    controversy_score: float
    factcheckability_score: float
    composite_score: float
    metadata: Dict[str, Any] = field(default_factory=dict)
