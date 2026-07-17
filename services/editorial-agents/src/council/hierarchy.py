from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class CouncilCycleError(RuntimeError):
    pass


@dataclass
class Answer:
    council_id: str
    question: str
    recommendation: str
    rationale: str
    confidence: float
    proposal_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CouncilNode:
    council: Any
    parent: CouncilNode | None = None
    children: list[CouncilNode] = field(default_factory=list)

    @property
    def council_id(self) -> str:
        return str(getattr(self.council, "council_id", ""))


class CouncilGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, CouncilNode] = {}

    def add_council(self, council: Any, parent: Any | None = None) -> CouncilNode:
        council_id = _council_id(council)
        node = self._nodes.get(council_id)
        if node is None:
            node = CouncilNode(council=council, parent=None, children=[])
            self._nodes[council_id] = node
        if parent is not None:
            parent_id = _council_id(parent)
            if parent_id not in self._nodes:
                self._nodes[parent_id] = CouncilNode(council=parent, parent=None, children=[])
            self.connect(parent_id=parent_id, child_id=council_id)
        return node

    def get_node(self, council_id: str) -> CouncilNode | None:
        return self._nodes.get(str(council_id or "").strip())

    def nodes(self) -> list[CouncilNode]:
        return list(self._nodes.values())

    def councils(self) -> list[Any]:
        return [node.council for node in self.nodes()]

    def connect(self, *, parent_id: str, child_id: str) -> None:
        clean_parent = str(parent_id or "").strip()
        clean_child = str(child_id or "").strip()
        if not clean_parent or not clean_child:
            raise ValueError("parent_id and child_id must be non-empty")
        if clean_parent == clean_child:
            raise CouncilCycleError("self-cycle is not allowed")
        parent = self._nodes.get(clean_parent)
        child = self._nodes.get(clean_child)
        if parent is None or child is None:
            raise KeyError("both parent and child councils must be registered")
        if self._path_exists(start_id=clean_child, target_id=clean_parent):
            raise CouncilCycleError(f"cycle detected: {clean_parent} -> {clean_child}")

        if child.parent is not None and child in child.parent.children:
            child.parent.children.remove(child)
        child.parent = parent
        if child not in parent.children:
            parent.children.append(child)

    def delegate(self, question: str, from_council: Any, to_council: Any) -> Answer:
        clean_question = str(question or "").strip()
        if not clean_question:
            raise ValueError("question must be non-empty")
        from_node = self.add_council(from_council)
        to_node = self.add_council(to_council)
        self.connect(parent_id=from_node.council_id, child_id=to_node.council_id)

        payload = to_council._answer_question(clean_question, source="delegation", from_council_id=from_node.council_id)
        answer = Answer(
            council_id=to_node.council_id,
            question=clean_question,
            recommendation=str(payload.get("recommendation") or "reject"),
            rationale=str(payload.get("rationale") or ""),
            confidence=max(0.0, min(1.0, _as_float(payload.get("confidence"), default=0.0))),
            proposal_id=str(payload.get("proposal_id") or ""),
            metadata={"from_council_id": from_node.council_id, "delegation_id": str(uuid4())},
        )
        _log_council_event(
            from_council,
            "delegation_sent",
            {"question": clean_question, "to_council_id": to_node.council_id, "answer": answer.__dict__},
        )
        _log_council_event(
            to_council,
            "delegation_received",
            {"question": clean_question, "from_council_id": from_node.council_id, "answer": answer.__dict__},
        )
        return answer

    def aggregate(self, child_answers: list[Answer | dict[str, Any]]) -> Answer:
        normalized = [_answer_to_dict(row) for row in list(child_answers or [])]
        if not normalized:
            raise ValueError("child_answers must be non-empty")

        approve = 0.0
        reject = 0.0
        rationales: list[str] = []
        for row in normalized:
            rec = str(row.get("recommendation") or "").strip().lower()
            confidence = max(0.0, min(1.0, _as_float(row.get("confidence"), default=0.0)))
            if rec == "approve":
                approve += confidence
            else:
                reject += confidence
            rationale = str(row.get("rationale") or "").strip()
            if rationale:
                rationales.append(rationale)

        recommendation = "approve" if approve >= reject else "reject"
        total = approve + reject
        confidence = 0.0 if total <= 0 else max(approve, reject) / total
        question = str(normalized[0].get("question") or "")
        return Answer(
            council_id="aggregate",
            question=question,
            recommendation=recommendation,
            rationale=" | ".join(rationales[:3]),
            confidence=confidence,
            metadata={"child_count": len(normalized)},
        )

    def _path_exists(self, *, start_id: str, target_id: str) -> bool:
        visited: set[str] = set()
        stack = [str(start_id)]
        while stack:
            current = stack.pop()
            if current == str(target_id):
                return True
            if current in visited:
                continue
            visited.add(current)
            node = self._nodes.get(current)
            if node is None:
                continue
            stack.extend(child.council_id for child in list(node.children))
        return False


def _log_council_event(council: Any, event_name: str, payload: dict[str, Any]) -> None:
    logger = getattr(council, "_log_event", None)
    if callable(logger):
        logger(event_name=str(event_name), payload=dict(payload))


def _council_id(council: Any) -> str:
    value = str(getattr(council, "council_id", "")).strip()
    if not value:
        raise ValueError("council.council_id must be non-empty")
    return value


def _answer_to_dict(answer: Answer | dict[str, Any]) -> dict[str, Any]:
    if isinstance(answer, Answer):
        return dict(answer.__dict__)
    if isinstance(answer, dict):
        return dict(answer)
    return {}


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)
