"""
memory/episode_lessons.py — Sprint A5: Self-Correction Foundation

Stores AssumptionErrors per episode and provides cross-episode retrieval
by domain and error_type.

╔══════════════════════════════════════════════════════════════╗
║  SPOR 2 — LEARNING TRACK ONLY                               ║
║  Publishes to memory.episode_lessons (Tier 2 Learning topic)║
║  Never imported by Spor 1 runtime modules.                  ║
╚══════════════════════════════════════════════════════════════╝

Redis key pattern
-----------------
  lessons:{episode_id}:{error_id}

Each key holds the JSON-serialised AssumptionError.

Lesson retrieval
----------------
``get_relevant_lessons(domain, error_type)`` scans all ``lessons:*`` keys,
deserialises each, and returns those matching both filters.
An empty string for either filter disables that filter (match all).

Kafka publishing (optional)
---------------------------
If a *publisher* callable is provided, each recorded lesson is also
published to ``memory.episode_lessons`` (Spor 2 topic).  In offline/test
mode, ``publisher=None`` and nothing is published.

Offline / test use
------------------
``InMemoryLessonsStore`` requires no Redis connection.
"""

from __future__ import annotations

import abc
import json
import logging
from dataclasses import asdict
from typing import Any, Callable, Dict, List, Optional

from babyai_shared.repair.assumption_repair import AssumptionError, ErrorType
from policy.policy_candidate import (
    PolicyCandidate,
    RecommendedAction,
    TriggerType,
)

logger = logging.getLogger(__name__)

_REDIS_PREFIX = "lessons"

# Publisher type: (topic: str, payload: dict) -> None
LessonPublisherFn = Callable[[str, Dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _error_to_dict(error: AssumptionError) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "error_id": error.error_id,
        "episode_id": error.episode_id,
        "error_type": error.error_type.value,
        "description": error.description,
        "repair_hint": error.repair_hint,
        "confidence_adjustment": error.confidence_adjustment,
        "domain": error.domain,
        "policy_evolution_candidate": (
            _candidate_to_dict(error.policy_evolution_candidate)
            if error.policy_evolution_candidate is not None
            else None
        ),
    }
    return d


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


def _error_from_dict(d: Dict[str, Any]) -> AssumptionError:
    candidate_data = d.get("policy_evolution_candidate")
    candidate = _candidate_from_dict(candidate_data) if candidate_data else None
    return AssumptionError(
        error_id=str(d["error_id"]),
        episode_id=str(d["episode_id"]),
        error_type=ErrorType(d["error_type"]),
        description=str(d.get("description", "")),
        repair_hint=str(d.get("repair_hint", "")),
        confidence_adjustment=float(d.get("confidence_adjustment", 0.0)),
        domain=str(d.get("domain", "")),
        policy_evolution_candidate=candidate,
    )


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class LessonsStore(abc.ABC):
    """Abstract store for AssumptionError persistence."""

    @abc.abstractmethod
    def save(self, error: AssumptionError) -> None:
        """Persist *error*."""

    @abc.abstractmethod
    def load_for_episode(self, episode_id: str) -> List[AssumptionError]:
        """Return all errors for *episode_id*."""

    @abc.abstractmethod
    def all_errors(self) -> List[AssumptionError]:
        """Return all persisted errors across all episodes."""


# ---------------------------------------------------------------------------
# Redis implementation
# ---------------------------------------------------------------------------


class RedisLessonsStore(LessonsStore):
    """Redis-backed lessons store.

    Key: ``lessons:{episode_id}:{error_id}``  Value: JSON.
    Uses SCAN to retrieve all errors for an episode or across all episodes.
    """

    def __init__(
        self,
        *,
        url: Optional[str] = None,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        try:
            import redis  # type: ignore
        except Exception as exc:
            raise ImportError("redis package required for RedisLessonsStore") from exc

        self._redis = redis.Redis.from_url(url) if url else redis.Redis(host=host, port=port, db=db)
        self._ttl = ttl_seconds

    def save(self, error: AssumptionError) -> None:
        key = f"{_REDIS_PREFIX}:{error.episode_id}:{error.error_id}"
        payload = json.dumps(_error_to_dict(error), ensure_ascii=True)
        if self._ttl is not None:
            self._redis.setex(key, self._ttl, payload)
        else:
            self._redis.set(key, payload)

    def load_for_episode(self, episode_id: str) -> List[AssumptionError]:
        pattern = f"{_REDIS_PREFIX}:{episode_id}:*"
        return self._scan_and_parse(pattern)

    def all_errors(self) -> List[AssumptionError]:
        pattern = f"{_REDIS_PREFIX}:*"
        return self._scan_and_parse(pattern)

    def _scan_and_parse(self, pattern: str) -> List[AssumptionError]:
        errors: List[AssumptionError] = []
        for key in self._redis.scan_iter(pattern):
            raw = self._redis.get(key)
            if raw is None:
                continue
            try:
                errors.append(_error_from_dict(json.loads(raw)))
            except Exception as exc:
                logger.warning("lessons_store parse_failed key=%s error=%s", key, exc)
        return errors


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------


class InMemoryLessonsStore(LessonsStore):
    """Non-persistent lessons store for testing and offline use."""

    def __init__(self) -> None:
        self._store: Dict[str, List[AssumptionError]] = {}

    def save(self, error: AssumptionError) -> None:
        self._store.setdefault(error.episode_id, []).append(error)

    def load_for_episode(self, episode_id: str) -> List[AssumptionError]:
        return list(self._store.get(episode_id, []))

    def all_errors(self) -> List[AssumptionError]:
        out: List[AssumptionError] = []
        for errors in self._store.values():
            out.extend(errors)
        return out


# ---------------------------------------------------------------------------
# EpisodeLessons facade
# ---------------------------------------------------------------------------


class EpisodeLessons:
    """Facade for recording and retrieving episode lessons.

    Parameters
    ----------
    store:
        Backend store (Redis or InMemory).
    publisher:
        Optional callable ``(topic, payload) -> None`` for publishing
        each lesson to ``memory.episode_lessons``.  Pass ``None`` for
        offline use.
    """

    def __init__(
        self,
        store: LessonsStore,
        *,
        publisher: Optional[LessonPublisherFn] = None,
    ) -> None:
        self._store = store
        self._publisher = publisher

    def record(self, error: AssumptionError) -> None:
        """Persist *error* and optionally publish to the lessons topic."""
        self._store.save(error)

        if self._publisher is not None:
            self._publish(error)

    def get_for_episode(self, episode_id: str) -> List[AssumptionError]:
        """Return all errors recorded for *episode_id*."""
        return self._store.load_for_episode(episode_id)

    def get_relevant_lessons(
        self,
        domain: str,
        error_type: str,
    ) -> List[AssumptionError]:
        """Return lessons matching *domain* and *error_type*.

        Parameters
        ----------
        domain:
            Domain label to filter on.  Empty string → match all domains.
        error_type:
            ErrorType value string (e.g. ``"WRONG_EFFECT"``).
            Empty string → match all error types.

        Returns
        -------
        List of AssumptionErrors that match both filters.
        """
        all_errors = self._store.all_errors()
        result: List[AssumptionError] = []
        for error in all_errors:
            if domain and error.domain != domain:
                continue
            if error_type and error.error_type.value != error_type:
                continue
            result.append(error)
        return result

    def _publish(self, error: AssumptionError) -> None:
        try:
            from bus.topics import MEMORY_EPISODE_LESSONS

            payload = _error_to_dict(error)
            assert self._publisher is not None
            self._publisher(MEMORY_EPISODE_LESSONS, payload)
        except Exception as exc:
            logger.warning("lessons_publish_failed episode=%s error=%s", error.episode_id, exc)
