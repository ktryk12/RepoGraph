"""
policy/policy_candidate.py — Sprint A3: Policy Bridge

Data contracts for the JEPA → policy bridge.

NoveltyInterpreter translates a (LatentPacket, PredictedState) pair into a
PolicyCandidate, which EnergyBasedECB then evaluates into a PolicyEvaluation.

These types live in the policy/ package (Spor 1-compatible data layer).
They carry no Kafka, no HTTP, no heavy runtime dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Tuple


class TriggerType(str, Enum):
    """What caused the novelty interpreter to flag this packet."""

    ANOMALY = "ANOMALY"               # novelty_score >= anomaly_threshold
    DRIFT = "DRIFT"                   # drift_threshold <= novelty_score < anomaly_threshold
    PREDICTION_ERROR = "PREDICTION_ERROR"  # predictor distance unusually high
    NORMAL = "NORMAL"                 # novelty_score below all thresholds


class RecommendedAction(str, Enum):
    """Action recommended by the policy layer for this candidate."""

    ALLOW = "ALLOW"                   # proceed without intervention
    OBSERVE = "OBSERVE"               # log and monitor, no gate
    ROUTE_TO_SPECIALIST = "ROUTE_TO_SPECIALIST"  # forward to domain expert
    REQUIRES_REVIEW = "REQUIRES_REVIEW"          # human review required
    SOFT_BLOCK = "SOFT_BLOCK"         # block with override path


@dataclass(frozen=True)
class PolicyCandidate:
    """A novel observation candidate flagged for policy evaluation.

    Produced by NoveltyInterpreter from a (LatentPacket, PredictedState) pair.

    Attributes
    ----------
    candidate_id:
        Unique identifier (typically the source packet_id).
    trigger_type:
        Why this candidate was created — see TriggerType.
    novelty_score:
        Raw novelty score from the LatentPacket (0.0–1.0).
    confidence:
        Predictor confidence at time of flagging (0.0–1.0).
    possible_new_dimension:
        Optional human-readable description of the novelty dimension.
    examples:
        Tuple of example strings grounding the novelty claim.
    recommended_action:
        Initial recommendation; EnergyBasedECB may override upward.
    """

    candidate_id: str
    trigger_type: TriggerType
    novelty_score: float
    confidence: float
    possible_new_dimension: str = ""
    examples: Tuple[str, ...] = field(default_factory=tuple)
    recommended_action: RecommendedAction = RecommendedAction.OBSERVE


@dataclass(frozen=True)
class PolicyEvaluation:
    """Result of EnergyBasedECB evaluating a PolicyCandidate.

    Attributes
    ----------
    candidate_id:
        Echoes the PolicyCandidate.candidate_id evaluated.
    matched_existing_rule:
        True if the novelty pattern matches a known policy rule.
    requires_review:
        True if human review is required (energy in review band).
    soft_gate:
        True if execution should be gated with an override path.
    hard_block:
        True if execution must be blocked unconditionally.
        When True, EnergyBasedECB raises ECBViolation.
    explanation:
        Human-readable summary of the evaluation decision.
    """

    candidate_id: str
    matched_existing_rule: bool
    requires_review: bool
    soft_gate: bool
    hard_block: bool
    explanation: str
