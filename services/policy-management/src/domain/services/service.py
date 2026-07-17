from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
import uuid

from policy.case_service import get_case_service
from policy.capabilities import Capability, normalize_capabilities
from policy.hash import hash_payload
from policy.reason_taxonomy_service import get_reason_taxonomy_service


@dataclass(frozen=True)
class PolicyDecision:
    decision_id: str
    input_hash: str
    action: str
    reasons: List[str]
    subject_type: str
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "input_hash": self.input_hash,
            "action": self.action,
            "reasons": list(self.reasons),
            "subject_type": self.subject_type,
            "timestamp": self.timestamp,
        }


def validate_question(question: Dict[str, Any], context: Optional[Dict[str, Any]] = None) -> PolicyDecision:
    reasons: List[str] = []
    action = "allow"

    if not isinstance(question, dict):
        return _deny("question", ["invalid_question_payload"])

    text = question.get("text")
    if not isinstance(text, str) or not text.strip():
        reasons.append("missing_text")
        action = "deny"

    if isinstance(text, str) and len(text) > 2000:
        reasons.append("question_too_long")
        action = "deny"

    payload = {"question": question, "context": context}
    return _decision("question", payload, action, reasons)


def validate_truth_proposal(
    proposal: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> PolicyDecision:
    reasons: List[str] = []
    action = "allow"

    if not isinstance(proposal, dict):
        return _deny("proposal", ["invalid_proposal_payload"])

    title = proposal.get("title")
    content = proposal.get("content")
    if not isinstance(title, str) or not title.strip():
        reasons.append("missing_title")
        action = "deny"
    if not isinstance(content, str) or not content.strip():
        reasons.append("missing_content")
        action = "deny"

    case_context = context if isinstance(context, dict) else {}
    case_id = get_case_service().resolve_case_id(
        case_id=_optional_text(proposal.get("case_id") if isinstance(proposal, dict) else None),
        context={**case_context, **(proposal if isinstance(proposal, dict) else {})},
    )
    payload = {
        "proposal": proposal,
        "context": context,
        "case_id": case_id,
    }
    return _decision("proposal", payload, action, reasons)


def validate_toolrun(
    request: Dict[str, Any],
    capabilities: Iterable[str | Capability],
) -> PolicyDecision:
    reasons: List[str] = []
    action = "allow"

    if not isinstance(request, dict):
        return _deny("toolrun", ["invalid_tool_request"])

    required = request.get("required_capability")
    if required is not None:
        required_cap = required.value if isinstance(required, Capability) else str(required)
        caps = set(normalize_capabilities(capabilities))
        if required_cap not in caps:
            reasons.append(f"missing_capability:{required_cap}")
            action = "deny"

    return _decision("toolrun", {"request": request, "capabilities": list(capabilities)}, action, reasons)


def _decision(subject_type: str, payload: Any, action: str, reasons: List[str]) -> PolicyDecision:
    get_reason_taxonomy_service().require(
        reasons,
        pack_name=f"policy.{subject_type}.reasons",
    )
    decision_id = f"policy-{uuid.uuid4()}"
    input_hash = hash_payload(payload)
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return PolicyDecision(
        decision_id=decision_id,
        input_hash=input_hash,
        action=action,
        reasons=reasons,
        subject_type=subject_type,
        timestamp=timestamp,
    )


def _deny(subject_type: str, reasons: List[str]) -> PolicyDecision:
    return _decision(subject_type, {"invalid": True}, "deny", reasons)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None
