from __future__ import annotations

import logging
from typing import Any, Iterable, List

logger = logging.getLogger(__name__)


class AnomalyDetectedError(Exception):
    pass


class AnomalyDetector:
    MAX_EXTREME_RATIO = 0.25
    MAX_PERFECT_CONF = 0.20
    EXTREME_SCORE_MARGIN = 0.02

    def check(self, votes: Iterable[Any]) -> None:
        rows = list(votes)
        if not rows:
            return
        self._check_extreme_scores(rows)
        self._check_perfect_confidence(rows)
        self._check_identical_votes(rows)

    def _check_extreme_scores(self, votes: List[Any]) -> None:
        total = len(votes)
        extreme_count = 0
        for vote in votes:
            score = _as_float(getattr(vote, "score_a", None), default=0.5)
            if score < self.EXTREME_SCORE_MARGIN or score > (1.0 - self.EXTREME_SCORE_MARGIN):
                extreme_count += 1
        ratio = float(extreme_count) / float(total)
        if ratio > self.MAX_EXTREME_RATIO:
            self._raise("Too many extreme scores", votes=votes)

    def _check_perfect_confidence(self, votes: List[Any]) -> None:
        total = len(votes)
        perfect_count = 0
        for vote in votes:
            confidence = _as_float(getattr(vote, "confidence", None), default=0.0)
            if confidence > 0.99:
                perfect_count += 1
        ratio = float(perfect_count) / float(total)
        if ratio > self.MAX_PERFECT_CONF:
            self._raise("Too many perfect confidence votes", votes=votes)

    def _check_identical_votes(self, votes: List[Any]) -> None:
        if len(votes) <= 2:
            return
        rounded = {round(_as_float(getattr(vote, "score_a", None), default=0.5), 4) for vote in votes}
        if len(rounded) == 1:
            self._raise("All votes identical", votes=votes)

    def _raise(self, message: str, *, votes: List[Any]) -> None:
        agent_ids = [str(getattr(v, "agent_id", "")) for v in votes]
        logger.error(
            "security_event event_type=anomaly_votes layer=5 error=%s vote_count=%s agent_ids=%s",
            message,
            len(votes),
            agent_ids,
        )
        raise AnomalyDetectedError(message)


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)
