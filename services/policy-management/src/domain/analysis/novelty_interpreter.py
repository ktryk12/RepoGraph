"""
policy/novelty_interpreter.py — Sprint A3: Policy Bridge

Translates a (LatentPacket, PredictedState) pair into a PolicyCandidate.

This module is Spor 1-safe: it imports only from core/ and policy/.
It must never be imported by eval/ or training/ modules.

Trigger logic
-------------
Given novelty_score from the LatentPacket:

  novelty_score >= anomaly_threshold  → ANOMALY  → SOFT_BLOCK
  drift_threshold <= score < anomaly  → DRIFT     → REQUIRES_REVIEW
  score < drift_threshold             → NORMAL    → ALLOW

PREDICTION_ERROR is triggered when the L2 distance between the predictor's
predicted_state_embedding and the packet's context_embedding exceeds
prediction_error_threshold, regardless of novelty_score.  In that case the
trigger type is PREDICTION_ERROR and the recommended action is OBSERVE.
"""

from __future__ import annotations

import logging
import math
from typing import Optional, Sequence

from babyai_shared.core.latent_packet import LatentPacket
from babyai_shared.core.jepa_predictor import PredictedState, _l2
from policy.policy_candidate import (
    PolicyCandidate,
    RecommendedAction,
    TriggerType,
)

logger = logging.getLogger(__name__)

# Default thresholds
_DEFAULT_ANOMALY_THRESHOLD: float = 0.8
_DEFAULT_DRIFT_THRESHOLD: float = 0.5
_DEFAULT_PREDICTION_ERROR_THRESHOLD: float = 0.3


class NoveltyInterpreter:
    """Translates latent observations into PolicyCandidates.

    Parameters
    ----------
    anomaly_threshold:
        novelty_score at or above which ANOMALY is triggered (default 0.8).
    drift_threshold:
        novelty_score at or above which DRIFT is triggered, below anomaly
        threshold (default 0.5).
    prediction_error_threshold:
        L2 distance between predicted and actual embeddings above which
        PREDICTION_ERROR is triggered (default 0.3).
    """

    def __init__(
        self,
        *,
        anomaly_threshold: float = _DEFAULT_ANOMALY_THRESHOLD,
        drift_threshold: float = _DEFAULT_DRIFT_THRESHOLD,
        prediction_error_threshold: float = _DEFAULT_PREDICTION_ERROR_THRESHOLD,
    ) -> None:
        if not (0.0 <= drift_threshold < anomaly_threshold <= 1.0):
            raise ValueError(
                f"Thresholds must satisfy 0 <= drift_threshold < anomaly_threshold <= 1; "
                f"got drift={drift_threshold}, anomaly={anomaly_threshold}"
            )
        self._anomaly_threshold = float(anomaly_threshold)
        self._drift_threshold = float(drift_threshold)
        self._prediction_error_threshold = float(prediction_error_threshold)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def interpret(
        self,
        packet: LatentPacket,
        predicted: PredictedState,
    ) -> PolicyCandidate:
        """Produce a PolicyCandidate from a packet + its predictor output.

        Parameters
        ----------
        packet:
            The observed LatentPacket (contains novelty_score, confidence,
            context_embedding).
        predicted:
            The PredictedState from JEPAPredictor for this packet's context.

        Returns
        -------
        PolicyCandidate with trigger_type and recommended_action set
        according to the threshold logic described in the module docstring.
        """
        novelty = float(packet.novelty_score)
        pred_dist = _l2(predicted.predicted_state_embedding, packet.context_embedding)

        # Determine trigger and recommended action
        if novelty >= self._anomaly_threshold:
            trigger = TriggerType.ANOMALY
            action = RecommendedAction.SOFT_BLOCK
            dimension = f"novelty_score={novelty:.4f} exceeds anomaly threshold={self._anomaly_threshold}"
        elif novelty >= self._drift_threshold:
            trigger = TriggerType.DRIFT
            action = RecommendedAction.REQUIRES_REVIEW
            dimension = f"novelty_score={novelty:.4f} in drift band [{self._drift_threshold}, {self._anomaly_threshold})"
        elif pred_dist > self._prediction_error_threshold:
            trigger = TriggerType.PREDICTION_ERROR
            action = RecommendedAction.OBSERVE
            dimension = f"prediction_error={pred_dist:.4f} exceeds threshold={self._prediction_error_threshold}"
        else:
            trigger = TriggerType.NORMAL
            action = RecommendedAction.ALLOW
            dimension = ""

        candidate = PolicyCandidate(
            candidate_id=packet.packet_id,
            trigger_type=trigger,
            novelty_score=novelty,
            confidence=float(packet.confidence),
            possible_new_dimension=dimension,
            examples=(),
            recommended_action=action,
        )

        if trigger != TriggerType.NORMAL:
            logger.info(
                "novelty_interpreter trigger=%s action=%s novelty=%.4f pred_dist=%.4f packet=%s",
                trigger.value,
                action.value,
                novelty,
                pred_dist,
                packet.packet_id,
            )

        return candidate

    # ------------------------------------------------------------------
    # Properties (for inspection / testing)
    # ------------------------------------------------------------------

    @property
    def anomaly_threshold(self) -> float:
        return self._anomaly_threshold

    @property
    def drift_threshold(self) -> float:
        return self._drift_threshold

    @property
    def prediction_error_threshold(self) -> float:
        return self._prediction_error_threshold
