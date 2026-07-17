"""
policy/ecb_energy.py — Sprint A3: Policy Bridge

Energy-Based ECB (Epistemic Circuit Breaker) — graduated risk gate.

Wraps the existing binary approval_required() gate with a graduated energy
score that maps a PolicyCandidate's novelty and trigger type to one of four
risk bands.  The binary gate is preserved as a fallback: if ecb_gate is None
in run_episode.py, the existing approval_required() path is unchanged.

Energy bands
------------
  energy < 0.3   → ALLOW          (no intervention)
  0.3 <= e < 0.7 → REQUIRES_REVIEW (publish to decision.approval)
  0.7 <= e < 0.95 → SOFT_BLOCK    (block, override path available)
  energy >= 0.95  → HARD_BLOCK    (raise ECBViolation unconditionally)

Energy computation
------------------
Base energy comes from novelty_score.  TriggerType applies a multiplier:

  NORMAL           × 0.5   (dampened — predictor is confident)
  PREDICTION_ERROR × 0.8
  DRIFT            × 1.0
  ANOMALY          × 1.2   (amplified — treat as higher risk)

Energy is then clamped to [0.0, 1.0].

L7 boundary
-----------
The HARD_BLOCK threshold (0.95) must never be silently overridden by
configuring a higher anomaly_threshold in NoveltyInterpreter.  The energy
calculation is independent of interpreter thresholds — it uses novelty_score
directly.  This ensures no single threshold parameter can neutralise the
hard block.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from policy.policy_candidate import (
    PolicyCandidate,
    PolicyEvaluation,
    RecommendedAction,
    TriggerType,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class ECBViolation(Exception):
    """Raised when EnergyBasedECB determines energy >= HARD_BLOCK threshold.

    Attributes
    ----------
    candidate_id:
        The PolicyCandidate.candidate_id that triggered the violation.
    energy:
        The computed energy score that exceeded the threshold.
    evaluation:
        The full PolicyEvaluation that produced this violation.
    """

    def __init__(
        self,
        candidate_id: str,
        energy: float,
        evaluation: "PolicyEvaluation",
        message: str = "",
    ) -> None:
        self.candidate_id = candidate_id
        self.energy = energy
        self.evaluation = evaluation
        super().__init__(
            message
            or f"ECBViolation: hard_block for candidate={candidate_id} energy={energy:.4f}"
        )


# ---------------------------------------------------------------------------
# Publisher type alias
# ---------------------------------------------------------------------------

# (topic: str, payload: dict) -> None
ApprovalPublisherFn = Callable[[str, Dict[str, Any]], None]

# ---------------------------------------------------------------------------
# Energy thresholds
# ---------------------------------------------------------------------------

_ALLOW_THRESHOLD: float = 0.3
_REVIEW_THRESHOLD: float = 0.7
_SOFT_BLOCK_THRESHOLD: float = 0.95

# TriggerType → energy multiplier
_TRIGGER_MULTIPLIERS: Dict[TriggerType, float] = {
    TriggerType.NORMAL: 0.5,
    TriggerType.PREDICTION_ERROR: 0.8,
    TriggerType.DRIFT: 1.0,
    TriggerType.ANOMALY: 1.2,
}


# ---------------------------------------------------------------------------
# EnergyBasedECB
# ---------------------------------------------------------------------------


class EnergyBasedECB:
    """Graduated risk gate wrapping the JEPA novelty pipeline.

    Parameters
    ----------
    approval_publisher:
        Optional callable invoked with (topic, payload) when a candidate
        enters the REQUIRES_REVIEW band.  Should publish to the
        ``decision.approval`` Kafka topic.  If None, review decisions are
        logged but not published.
    """

    def __init__(
        self,
        *,
        approval_publisher: Optional[ApprovalPublisherFn] = None,
    ) -> None:
        self._approval_publisher = approval_publisher

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(self, candidate: PolicyCandidate) -> PolicyEvaluation:
        """Evaluate a PolicyCandidate and return a PolicyEvaluation.

        Side effects
        ------------
        - If energy >= HARD_BLOCK threshold: raises ECBViolation immediately.
        - If REQUIRES_REVIEW band: publishes to decision.approval if publisher
          is configured.
        - All decisions are logged at INFO level.

        Parameters
        ----------
        candidate:
            The PolicyCandidate to evaluate.

        Returns
        -------
        PolicyEvaluation (only returned for ALLOW, REQUIRES_REVIEW, SOFT_BLOCK bands).

        Raises
        ------
        ECBViolation
            When computed energy >= 0.95 (HARD_BLOCK band).
        """
        energy = self._compute_energy(candidate)
        evaluation = self._make_evaluation(candidate, energy)

        logger.info(
            "ecb_energy candidate=%s trigger=%s novelty=%.4f energy=%.4f "
            "hard_block=%s soft_gate=%s requires_review=%s",
            candidate.candidate_id,
            candidate.trigger_type.value,
            candidate.novelty_score,
            energy,
            evaluation.hard_block,
            evaluation.soft_gate,
            evaluation.requires_review,
        )

        if evaluation.hard_block:
            raise ECBViolation(
                candidate_id=candidate.candidate_id,
                energy=energy,
                evaluation=evaluation,
            )

        if evaluation.requires_review and self._approval_publisher is not None:
            self._publish_review(candidate, energy)

        return evaluation

    def compute_energy(self, candidate: PolicyCandidate) -> float:
        """Return the energy score for a candidate without side effects.

        Useful for logging/inspection without triggering gates.
        """
        return self._compute_energy(candidate)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_energy(self, candidate: PolicyCandidate) -> float:
        multiplier = _TRIGGER_MULTIPLIERS.get(candidate.trigger_type, 1.0)
        raw = float(candidate.novelty_score) * multiplier
        return min(max(raw, 0.0), 1.0)

    def _make_evaluation(
        self, candidate: PolicyCandidate, energy: float
    ) -> PolicyEvaluation:
        if energy >= _SOFT_BLOCK_THRESHOLD:
            return PolicyEvaluation(
                candidate_id=candidate.candidate_id,
                matched_existing_rule=False,
                requires_review=False,
                soft_gate=False,
                hard_block=True,
                explanation=(
                    f"HARD_BLOCK: energy={energy:.4f} >= threshold={_SOFT_BLOCK_THRESHOLD}. "
                    f"trigger={candidate.trigger_type.value} novelty={candidate.novelty_score:.4f}"
                ),
            )
        elif energy >= _REVIEW_THRESHOLD:
            return PolicyEvaluation(
                candidate_id=candidate.candidate_id,
                matched_existing_rule=False,
                requires_review=False,
                soft_gate=True,
                hard_block=False,
                explanation=(
                    f"SOFT_BLOCK: energy={energy:.4f} in [{_REVIEW_THRESHOLD}, {_SOFT_BLOCK_THRESHOLD}). "
                    f"trigger={candidate.trigger_type.value}"
                ),
            )
        elif energy >= _ALLOW_THRESHOLD:
            return PolicyEvaluation(
                candidate_id=candidate.candidate_id,
                matched_existing_rule=False,
                requires_review=True,
                soft_gate=False,
                hard_block=False,
                explanation=(
                    f"REQUIRES_REVIEW: energy={energy:.4f} in [{_ALLOW_THRESHOLD}, {_REVIEW_THRESHOLD}). "
                    f"trigger={candidate.trigger_type.value}"
                ),
            )
        else:
            return PolicyEvaluation(
                candidate_id=candidate.candidate_id,
                matched_existing_rule=True,
                requires_review=False,
                soft_gate=False,
                hard_block=False,
                explanation=(
                    f"ALLOW: energy={energy:.4f} < threshold={_ALLOW_THRESHOLD}. "
                    f"trigger={candidate.trigger_type.value}"
                ),
            )

    def _publish_review(
        self, candidate: PolicyCandidate, energy: float
    ) -> None:
        """Publish a review request to decision.approval."""
        try:
            from bus.topics import DECISION_APPROVAL

            payload: Dict[str, Any] = {
                "candidate_id": candidate.candidate_id,
                "trigger_type": candidate.trigger_type.value,
                "novelty_score": candidate.novelty_score,
                "confidence": candidate.confidence,
                "energy": energy,
                "possible_new_dimension": candidate.possible_new_dimension,
                "recommended_action": candidate.recommended_action.value,
            }
            assert self._approval_publisher is not None
            self._approval_publisher(DECISION_APPROVAL, payload)
        except Exception as exc:
            logger.warning("ecb_energy_publish_failed error=%s", exc)
