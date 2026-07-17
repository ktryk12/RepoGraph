from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from babyai.council.hierarchy import CouncilCycleError


@dataclass(frozen=True)
class ConsultationResult:
    id: str
    session_id: str
    from_council_id: str
    to_council_id: str
    question: str
    recommendation: str
    rationale: str
    confidence: float
    answer_payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class LateralBus:
    def __init__(self) -> None:
        self._session_edges: dict[str, set[tuple[str, str]]] = {}

    def consult(self, question: str, from_council: Any, to_council: Any) -> ConsultationResult:
        clean_question = str(question or "").strip()
        if not clean_question:
            raise ValueError("question must be non-empty")
        from_id = _council_id(from_council)
        to_id = _council_id(to_council)
        if from_id == to_id:
            raise CouncilCycleError("self-consultation is not allowed")

        session_id = str(getattr(from_council, "_active_deliberation_session", "") or "default")
        edges = self._session_edges.setdefault(session_id, set())
        if _would_create_cycle(edges=edges, new_edge=(from_id, to_id)):
            raise CouncilCycleError(f"lateral cycle detected in session {session_id}: {from_id}->{to_id}")
        edges.add((from_id, to_id))

        # Propagate deliberation-session context to preserve cycle checks across chained consultations.
        setattr(to_council, "_active_deliberation_session", session_id)
        answer_payload = to_council._answer_question(clean_question, source="lateral_consultation", from_council_id=from_id)
        setattr(to_council, "_active_deliberation_session", session_id)
        result = ConsultationResult(
            id=str(uuid4()),
            session_id=session_id,
            from_council_id=from_id,
            to_council_id=to_id,
            question=clean_question,
            recommendation=str(answer_payload.get("recommendation") or "reject"),
            rationale=str(answer_payload.get("rationale") or ""),
            confidence=max(0.0, min(1.0, _as_float(answer_payload.get("confidence"), default=0.0))),
            answer_payload=dict(answer_payload),
        )
        _log_council_event(
            from_council,
            "lateral_consult_sent",
            {"session_id": session_id, "to_council_id": to_id, "question": clean_question, "result": result.__dict__},
        )
        _log_council_event(
            to_council,
            "lateral_consult_received",
            {"session_id": session_id, "from_council_id": from_id, "question": clean_question, "result": result.__dict__},
        )
        return result


def _would_create_cycle(*, edges: set[tuple[str, str]], new_edge: tuple[str, str]) -> bool:
    from_id, to_id = new_edge
    if from_id == to_id:
        return True
    adjacency: dict[str, set[str]] = {}
    for left, right in list(edges) + [new_edge]:
        adjacency.setdefault(str(left), set()).add(str(right))
    stack = [str(to_id)]
    visited: set[str] = set()
    while stack:
        current = stack.pop()
        if current == str(from_id):
            return True
        if current in visited:
            continue
        visited.add(current)
        stack.extend(adjacency.get(current, set()))
    return False


def _council_id(council: Any) -> str:
    value = str(getattr(council, "council_id", "")).strip()
    if not value:
        raise ValueError("council.council_id must be non-empty")
    return value


def _log_council_event(council: Any, event_name: str, payload: dict[str, Any]) -> None:
    logger = getattr(council, "_log_event", None)
    if callable(logger):
        logger(event_name=str(event_name), payload=dict(payload))


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)
