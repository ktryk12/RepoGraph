"""
policy/policy_evolution_queue.py — Sprint A5: Self-Correction Foundation

Receives PolicyCandidates from AssumptionRepair and queues them for
human review.  NEVER auto-applies any policy change.

╔══════════════════════════════════════════════════════════════╗
║  HUMAN REVIEW REQUIRED                                      ║
║  approve() and reject() only publish to policy.approved /   ║
║  policy.rejected topics.  They do NOT modify any policy.   ║
║  A separate human-reviewed promotion step (A6+) consumes    ║
║  those topics and applies changes.                          ║
╚══════════════════════════════════════════════════════════════╝

Redis storage
-------------
  Hash key : ``policy_evolution_pending``
  Field    : candidate_id (str)
  Value    : JSON-serialised PolicyCandidate

This is a Redis Hash so that ``list_pending()`` (HVALS), ``enqueue()``
(HSET), and ``approve``/``reject`` (HDEL + publish) are all O(1) or O(N)
on the number of pending candidates — not O(total key space).

Offline / test use
------------------
``InMemoryPolicyEvolutionQueue`` provides an equivalent implementation
that needs no Redis connection.
"""

from __future__ import annotations

import abc
import json
import logging
from typing import Any, Callable, Dict, List, Optional

from policy.policy_candidate import (
    PolicyCandidate,
    RecommendedAction,
    TriggerType,
)

logger = logging.getLogger(__name__)

_REDIS_HASH_KEY = "policy_evolution_pending"

# Publisher type: (topic: str, payload: dict) -> None
EvolutionPublisherFn = Callable[[str, Dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Serialisation helpers (reused from episode_lessons pattern)
# ---------------------------------------------------------------------------


def _candidate_to_dict(c: PolicyCandidate) -> Dict[str, Any]:
    return {
        "candidate_id": c.candidate_id,
        "trigger_type": c.trigger_type.value,
        "novelty_score": c.novelty_score,
        "confidence": c.confidence,
        "possible_new_dimension": c.possible_new_dimension,
        "examples": list(c.examples),
        "recommended_action": c.recommended_action.value,
    }


def _candidate_from_dict(d: Dict[str, Any]) -> PolicyCandidate:
    return PolicyCandidate(
        candidate_id=str(d["candidate_id"]),
        trigger_type=TriggerType(d["trigger_type"]),
        novelty_score=float(d["novelty_score"]),
        confidence=float(d["confidence"]),
        possible_new_dimension=str(d.get("possible_new_dimension") or ""),
        examples=tuple(d.get("examples") or []),
        recommended_action=RecommendedAction(d["recommended_action"]),
    )


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class EvolutionQueueBackend(abc.ABC):
    """Abstract backend for PolicyEvolutionQueue storage."""

    @abc.abstractmethod
    def put(self, candidate_id: str, payload: str) -> None:
        """Store or update a candidate by ID."""

    @abc.abstractmethod
    def get_all(self) -> List[str]:
        """Return all serialised candidate payloads."""

    @abc.abstractmethod
    def remove(self, candidate_id: str) -> bool:
        """Remove a candidate by ID.  Returns True if it existed."""


# ---------------------------------------------------------------------------
# Redis backend
# ---------------------------------------------------------------------------


class RedisEvolutionQueueBackend(EvolutionQueueBackend):
    """Redis Hash-backed queue backend."""

    def __init__(
        self,
        *,
        url: Optional[str] = None,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
    ) -> None:
        try:
            import redis  # type: ignore
        except Exception as exc:
            raise ImportError("redis package required for RedisEvolutionQueueBackend") from exc

        self._redis = redis.Redis.from_url(url) if url else redis.Redis(host=host, port=port, db=db)

    def put(self, candidate_id: str, payload: str) -> None:
        self._redis.hset(_REDIS_HASH_KEY, candidate_id, payload)

    def get_all(self) -> List[str]:
        raw_map = self._redis.hgetall(_REDIS_HASH_KEY)
        return [
            v.decode("utf-8") if isinstance(v, bytes) else str(v)
            for v in raw_map.values()
        ]

    def remove(self, candidate_id: str) -> bool:
        return bool(self._redis.hdel(_REDIS_HASH_KEY, candidate_id))


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


class InMemoryEvolutionQueueBackend(EvolutionQueueBackend):
    """Non-persistent backend for testing and offline use."""

    def __init__(self) -> None:
        self._data: Dict[str, str] = {}

    def put(self, candidate_id: str, payload: str) -> None:
        self._data[candidate_id] = payload

    def get_all(self) -> List[str]:
        return list(self._data.values())

    def remove(self, candidate_id: str) -> bool:
        if candidate_id in self._data:
            del self._data[candidate_id]
            return True
        return False


# ---------------------------------------------------------------------------
# PolicyEvolutionQueue facade
# ---------------------------------------------------------------------------


class PolicyEvolutionQueue:
    """Queue of PolicyCandidates awaiting human review.

    This class DOES NOT modify any policy.  ``approve()`` and ``reject()``
    only publish to ``policy.approved`` / ``policy.rejected`` topics.
    The consumer of those topics is responsible for any policy change,
    and that consumer requires an explicit human promotion step.

    Parameters
    ----------
    backend:
        Storage backend (Redis or InMemory).
    publisher:
        Optional callable ``(topic, payload) -> None`` invoked by
        ``approve()`` and ``reject()``.  Pass ``None`` for offline use
        (approve/reject will log but not publish).
    """

    def __init__(
        self,
        backend: EvolutionQueueBackend,
        *,
        publisher: Optional[EvolutionPublisherFn] = None,
    ) -> None:
        self._backend = backend
        self._publisher = publisher

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def enqueue(self, candidate: PolicyCandidate) -> None:
        """Add *candidate* to the pending queue.

        Idempotent: enqueueing the same candidate_id again overwrites
        the existing entry.
        """
        payload = json.dumps(_candidate_to_dict(candidate), ensure_ascii=True)
        self._backend.put(candidate.candidate_id, payload)
        logger.info(
            "evolution_queue enqueued candidate=%s trigger=%s action=%s",
            candidate.candidate_id,
            candidate.trigger_type.value,
            candidate.recommended_action.value,
        )

    def list_pending(self) -> List[PolicyCandidate]:
        """Return all candidates currently awaiting review."""
        candidates: List[PolicyCandidate] = []
        for raw in self._backend.get_all():
            try:
                candidates.append(_candidate_from_dict(json.loads(raw)))
            except Exception as exc:
                logger.warning("evolution_queue parse_failed error=%s", exc)
        return candidates

    def approve(self, candidate_id: str) -> bool:
        """Mark *candidate_id* as approved and publish to policy.approved.

        Does NOT modify any policy.  Returns True if the candidate was
        found and removed from the queue, False if it was not found.

        The ``policy.approved`` topic consumer is responsible for any
        downstream policy application, gated by human review.
        """
        return self._resolve(candidate_id, approved=True)

    def reject(self, candidate_id: str) -> bool:
        """Mark *candidate_id* as rejected and publish to policy.rejected.

        Returns True if found, False otherwise.
        """
        return self._resolve(candidate_id, approved=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, candidate_id: str, *, approved: bool) -> bool:
        # Find the candidate first
        candidate: Optional[PolicyCandidate] = None
        for raw in self._backend.get_all():
            try:
                c = _candidate_from_dict(json.loads(raw))
                if c.candidate_id == candidate_id:
                    candidate = c
                    break
            except Exception:
                continue

        removed = self._backend.remove(candidate_id)
        if not removed:
            logger.warning("evolution_queue resolve: candidate not found id=%s", candidate_id)
            return False

        action_name = "approved" if approved else "rejected"
        logger.info("evolution_queue %s candidate=%s", action_name, candidate_id)

        if self._publisher is not None and candidate is not None:
            self._publish_resolution(candidate, approved=approved)

        return True

    def _publish_resolution(self, candidate: PolicyCandidate, *, approved: bool) -> None:
        try:
            from bus.topics import POLICY_APPROVED, POLICY_REJECTED

            topic = POLICY_APPROVED if approved else POLICY_REJECTED
            payload: Dict[str, Any] = {
                **_candidate_to_dict(candidate),
                "resolution": "approved" if approved else "rejected",
                "requires_human_promotion": True,  # explicit: no auto-apply
            }
            assert self._publisher is not None
            self._publisher(topic, payload)
        except Exception as exc:
            logger.warning("evolution_queue publish_failed error=%s", exc)
