"""
memory/world_state_snapshot.py — Sprint A4: Replay Engine

Captures world state before and after an episode action, computes the delta
between the two states, and persists snapshots to Redis (DB 0) for later
replay and lesson extraction.

Redis key pattern
-----------------
  world_state:{episode_id}:{snapshot_id}

Each key holds the JSON-serialised WorldStateSnapshot.  Listing all
snapshots for an episode uses SCAN with the pattern ``world_state:{episode_id}:*``.

Offline / test use
------------------
``InMemorySnapshotStore`` provides an offline implementation that requires
no Redis connection.  Use it in tests and anywhere Redis is not available.

Storage protocol
----------------
Both ``RedisSnapshotStore`` and ``InMemorySnapshotStore`` satisfy the
``SnapshotStore`` abstract base class.  Pass either to ``ReplayRunner`` or
``RunEpisodeUseCase`` interchangeably.
"""

from __future__ import annotations

import abc
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)

_REDIS_KEY_PREFIX = "world_state"


# ---------------------------------------------------------------------------
# Core data contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorldStateSnapshot:
    """Captures world state before and after an episode action.

    Attributes
    ----------
    snapshot_id:
        Unique identifier for this snapshot (UUID).
    episode_id:
        The episode this snapshot belongs to.
    timestamp:
        ISO-8601 UTC string at time of capture.
    world_state_before:
        Serialisable dict representing world state before the action.
    world_state_after:
        Serialisable dict representing world state after the action.
    predicted_world_delta:
        What the JEPA predictor expected to change (populated in A5;
        empty dict until then).
    actual_world_delta:
        Actual changes as computed by ``compute_delta(before, after)``.
    artifact_ref:
        Optional reference to a persisted artifact in FileArtifactStore.
        Format: ``artifact:sha256:<64-hex>`` or empty string.
    """

    snapshot_id: str
    episode_id: str
    timestamp: str
    world_state_before: Dict[str, Any]
    world_state_after: Dict[str, Any]
    predicted_world_delta: Dict[str, Any]
    actual_world_delta: Dict[str, Any]
    artifact_ref: str = ""

    # ------------------------------------------------------------------
    # Delta computation
    # ------------------------------------------------------------------

    @staticmethod
    def compute_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
        """Return only changed keys between *before* and *after*.

        Rules
        -----
        - Keys present in *after* but not *before* → ``{"added": <value>}``
        - Keys present in *before* but not *after* → ``{"removed": <value>}``
        - Keys present in both but with different values:
          - If both values are dicts → recurse; include key only if nested
            delta is non-empty.
          - Otherwise → ``{"before": <old>, "after": <new>}``
        - Equal values → omitted.

        Parameters
        ----------
        before:
            World state snapshot before the action.
        after:
            World state snapshot after the action.

        Returns
        -------
        Dict containing only the changed entries.  Empty dict means no
        observable change.
        """
        delta: Dict[str, Any] = {}
        all_keys = set(before) | set(after)
        for key in all_keys:
            if key not in before:
                delta[key] = {"added": after[key]}
            elif key not in after:
                delta[key] = {"removed": before[key]}
            elif before[key] != after[key]:
                if isinstance(before[key], dict) and isinstance(after[key], dict):
                    nested = WorldStateSnapshot.compute_delta(before[key], after[key])
                    if nested:
                        delta[key] = nested
                else:
                    delta[key] = {"before": before[key], "after": after[key]}
        return delta

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorldStateSnapshot":
        return cls(
            snapshot_id=str(d["snapshot_id"]),
            episode_id=str(d["episode_id"]),
            timestamp=str(d["timestamp"]),
            world_state_before=dict(d.get("world_state_before") or {}),
            world_state_after=dict(d.get("world_state_after") or {}),
            predicted_world_delta=dict(d.get("predicted_world_delta") or {}),
            actual_world_delta=dict(d.get("actual_world_delta") or {}),
            artifact_ref=str(d.get("artifact_ref") or ""),
        )


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def make_snapshot(
    episode_id: str,
    world_state_before: Dict[str, Any],
    world_state_after: Dict[str, Any],
    *,
    predicted_world_delta: Optional[Dict[str, Any]] = None,
    artifact_ref: str = "",
) -> WorldStateSnapshot:
    """Convenience factory: create a WorldStateSnapshot with delta computed."""
    actual_delta = WorldStateSnapshot.compute_delta(world_state_before, world_state_after)
    return WorldStateSnapshot(
        snapshot_id=str(uuid4()),
        episode_id=episode_id,
        timestamp=_now_iso(),
        world_state_before=world_state_before,
        world_state_after=world_state_after,
        predicted_world_delta=predicted_world_delta or {},
        actual_world_delta=actual_delta,
        artifact_ref=artifact_ref,
    )


# ---------------------------------------------------------------------------
# SnapshotStore abstract base
# ---------------------------------------------------------------------------


class SnapshotStore(abc.ABC):
    """Abstract store for WorldStateSnapshot persistence."""

    @abc.abstractmethod
    def save(self, snapshot: WorldStateSnapshot) -> None:
        """Persist *snapshot*."""

    @abc.abstractmethod
    def load_for_episode(self, episode_id: str) -> List[WorldStateSnapshot]:
        """Return all snapshots for *episode_id*, oldest first."""


# ---------------------------------------------------------------------------
# Redis implementation
# ---------------------------------------------------------------------------


class RedisSnapshotStore(SnapshotStore):
    """Redis-backed snapshot store (DB 0 by default).

    Lazy import: ``redis`` is only imported when this class is instantiated.

    Parameters
    ----------
    url:
        Redis URL (e.g. ``redis://localhost:6379/0``).  Takes precedence
        over *host*/*port*/*db* when provided.
    host / port / db:
        Direct connection parameters used when *url* is None.
    ttl_seconds:
        Optional TTL for each key.  ``None`` means no expiry.
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
            raise ImportError(
                "redis package is required for RedisSnapshotStore"
            ) from exc

        self._redis = redis.Redis.from_url(url) if url else redis.Redis(host=host, port=port, db=db)
        self._ttl = ttl_seconds

    def save(self, snapshot: WorldStateSnapshot) -> None:
        key = self._key(snapshot.episode_id, snapshot.snapshot_id)
        payload = json.dumps(snapshot.to_dict(), ensure_ascii=True, default=str)
        if self._ttl is not None:
            self._redis.setex(key, self._ttl, payload)
        else:
            self._redis.set(key, payload)
        logger.debug("snapshot_store saved key=%s", key)

    def load_for_episode(self, episode_id: str) -> List[WorldStateSnapshot]:
        pattern = f"{_REDIS_KEY_PREFIX}:{episode_id}:*"
        keys = list(self._redis.scan_iter(pattern))
        snapshots: List[WorldStateSnapshot] = []
        for key in keys:
            raw = self._redis.get(key)
            if raw is None:
                continue
            try:
                d = json.loads(raw)
                snapshots.append(WorldStateSnapshot.from_dict(d))
            except Exception as exc:
                logger.warning("snapshot_store load failed key=%s error=%s", key, exc)
        snapshots.sort(key=lambda s: s.timestamp)
        return snapshots

    @staticmethod
    def _key(episode_id: str, snapshot_id: str) -> str:
        return f"{_REDIS_KEY_PREFIX}:{episode_id}:{snapshot_id}"


# ---------------------------------------------------------------------------
# In-memory implementation (for tests and offline use)
# ---------------------------------------------------------------------------


class InMemorySnapshotStore(SnapshotStore):
    """Non-persistent snapshot store for testing and offline use."""

    def __init__(self) -> None:
        self._store: Dict[str, List[WorldStateSnapshot]] = {}

    def save(self, snapshot: WorldStateSnapshot) -> None:
        self._store.setdefault(snapshot.episode_id, []).append(snapshot)

    def load_for_episode(self, episode_id: str) -> List[WorldStateSnapshot]:
        items = list(self._store.get(episode_id, []))
        items.sort(key=lambda s: s.timestamp)
        return items

    def all_snapshots(self) -> List[WorldStateSnapshot]:
        """Return every stored snapshot across all episodes (test helper)."""
        out: List[WorldStateSnapshot] = []
        for snaps in self._store.values():
            out.extend(snaps)
        return out
