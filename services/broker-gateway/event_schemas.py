"""
Kafka event schemas with versioning and references.

Events use references (task_ref, artifact_ref), not full payloads.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from enum import Enum
import json
from hashlib import sha256
from typing import Any, Dict, List, Optional

from babyai_shared.privacy.gateway import PrivacyGateway


SCHEMA_VERSION = 1
_GATEWAY = PrivacyGateway.default()


class DecisionStatus(Enum):
    """Decision lifecycle states."""

    REQUESTED = "requested"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    STARTED = "started"
    GENERATING = "generating"
    EVALUATING = "evaluating"
    EVALUATED = "evaluated"
    REPAIRING = "repairing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class DecisionEvent:
    """
    Event for decision.lifecycle topic.

    Uses task_ref (not full task) to keep payload small.
    """

    schema_version: int
    decision_id: str
    context_id: str
    status: DecisionStatus
    timestamp: str
    task_ref: str
    truth_pack_ref: str
    truth_pack_version: str
    iteration: Optional[int] = None
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    event_id: Optional[str] = None
    content_hash: Optional[str] = None

    def to_json(self) -> str:
        data = _event_payload(self)
        # Canonical ID field for external contracts. Internal code still reads decision_id.
        data["episode_id"] = self.decision_id
        data["status"] = self.status.value
        return _serialize_event(self, data)

    @classmethod
    def from_json(cls, data: str) -> "DecisionEvent":
        obj = json.loads(data)
        episode_id = obj.pop("episode_id", None)
        if isinstance(episode_id, str) and episode_id.strip():
            # Canonicalize to internal field; decision_id is treated as derived/secondary.
            obj["decision_id"] = episode_id.strip()
        obj["status"] = DecisionStatus(obj["status"])
        return cls(**obj)


@dataclass
class EvalResultEvent:
    """
    Event for eval.results topic.

    Published after each evaluation completes.
    """

    schema_version: int
    decision_id: str
    context_id: str
    iteration: int
    timestamp: str
    passed: bool
    score: float
    components: Dict[str, float]
    gate_results: Dict[str, bool]
    penalties: List[str]
    failure_reasons: List[str]
    runner_used: Optional[str] = None
    expert_used: Optional[str] = None
    tokens_used: Optional[int] = None
    latency_ms: Optional[float] = None
    decision_ref: Optional[str] = None
    training_data: Optional[Dict[str, Any]] = None
    event_id: Optional[str] = None
    content_hash: Optional[str] = None

    def to_json(self) -> str:
        data = _event_payload(self)
        return _serialize_event(self, data)

    @classmethod
    def from_json(cls, data: str) -> "EvalResultEvent":
        return cls(**json.loads(data))


@dataclass
class ToolEvent:
    """Event for tool.events topic."""

    schema_version: int
    event_type: str
    decision_id: str
    context_id: str
    tool_name: str
    timestamp: str
    args_digest: Optional[str] = None
    result_ref: Optional[str] = None
    error: Optional[str] = None
    latency_ms: Optional[float] = None
    event_id: Optional[str] = None
    content_hash: Optional[str] = None

    def to_json(self) -> str:
        data = _event_payload(self)
        return _serialize_event(self, data)

    @classmethod
    def from_json(cls, data: str) -> "ToolEvent":
        return cls(**json.loads(data))


@dataclass
class ArtifactEvent:
    """Event for artifact.events topic."""

    schema_version: int
    event_type: str
    artifact_ref: str
    context_id: str
    artifact_type: str
    timestamp: str
    size_bytes: Optional[int] = None
    content_hash: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    event_id: Optional[str] = None

    def to_json(self) -> str:
        data = _event_payload(self)
        return _serialize_event(self, data)

    @classmethod
    def from_json(cls, data: str) -> "ArtifactEvent":
        return cls(**json.loads(data))


@dataclass
class ApprovalEvent:
    """Event for decision.approval topic."""

    schema_version: int
    decision_id: str
    policy_fingerprint: str
    approved_by: str
    approved_at: str
    approved: bool = True
    context_id: Optional[str] = None
    reason: Optional[str] = None
    event_id: Optional[str] = None
    content_hash: Optional[str] = None

    def to_json(self) -> str:
        data = _event_payload(self)
        return _serialize_event(self, data)

    @classmethod
    def from_json(cls, data: str) -> "ApprovalEvent":
        return cls(**json.loads(data))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _event_payload(event_obj: Any) -> Dict[str, Any]:
    data = asdict(event_obj)
    return {k: v for k, v in data.items() if v is not None}


def _serialize_event(event_obj: Any, data: Dict[str, Any]) -> str:
    payload = dict(data)
    payload.pop("event_id", None)
    payload.pop("content_hash", None)
    base = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    base = _GATEWAY.scrub_json_string(base)
    digest = sha256(base.encode("utf-8")).hexdigest()
    if getattr(event_obj, "event_id", None) is None:
        setattr(event_obj, "event_id", digest)
    if getattr(event_obj, "content_hash", None) is None:
        setattr(event_obj, "content_hash", digest)
    payload["event_id"] = getattr(event_obj, "event_id")
    payload["content_hash"] = getattr(event_obj, "content_hash")
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _GATEWAY.scrub_json_string(serialized)
