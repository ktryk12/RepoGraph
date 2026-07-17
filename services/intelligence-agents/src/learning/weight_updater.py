from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import asyncio
from typing import Any
from uuid import uuid4

from babyai.learning.pattern_agent import Pattern


@dataclass
class WeightUpdateProposal:
    id: str
    agent_role: str
    old_weight: float
    new_weight: float
    justification: str
    pattern_ref: dict[str, Any]
    reviewed_by_council: bool = False
    review_notes: str = ""
    created_at: str = field(default_factory=lambda: _utc_now_iso())
    applied_at: str | None = None
    rolled_back_at: str | None = None


class WeightUpdater:
    def __init__(self, consensus_engine_ref: Any, memory_ref: Any) -> None:
        self.consensus_engine_ref = consensus_engine_ref
        self.memory_ref = memory_ref
        self._applied_proposals: dict[str, WeightUpdateProposal] = {}

    def propose_update(self, pattern: Pattern) -> WeightUpdateProposal:
        role = _role_from_pattern(pattern)
        old_weight = _get_weight(self.consensus_engine_ref, role)
        delta = _compute_delta(pattern)
        new_weight = max(0.05, min(3.0, old_weight + delta))
        proposal = WeightUpdateProposal(
            id=str(uuid4()),
            agent_role=role,
            old_weight=float(old_weight),
            new_weight=float(new_weight),
            justification=_justification(pattern=pattern, delta=delta),
            pattern_ref={
                "feature_combo": dict(pattern.feature_combo),
                "outcome": str(pattern.outcome),
                "hit_rate": float(pattern.hit_rate),
                "sample_size": int(pattern.sample_size),
                "confidence": float(pattern.confidence),
                "discovered_at": str(pattern.discovered_at),
                "expires_at": str(pattern.expires_at),
            },
        )
        self._log_event(
            subtype="weight_update_proposed",
            payload={
                "proposal": proposal.__dict__,
            },
            proposal=proposal,
        )
        return proposal

    def apply(self, proposal: WeightUpdateProposal) -> None:
        if not isinstance(proposal, WeightUpdateProposal):
            raise TypeError("proposal must be WeightUpdateProposal")
        if not proposal.reviewed_by_council:
            reviewed, notes = self._review_with_council(proposal)
            proposal.reviewed_by_council = reviewed
            proposal.review_notes = notes
        if not proposal.reviewed_by_council:
            raise PermissionError("weight update requires council review")

        _set_weight(self.consensus_engine_ref, proposal.agent_role, float(proposal.new_weight))
        proposal.applied_at = _utc_now_iso()
        self._applied_proposals[proposal.id] = proposal
        self._log_event(
            subtype="weight_update_applied",
            payload={
                "proposal_id": proposal.id,
                "agent_role": proposal.agent_role,
                "old_weight": float(proposal.old_weight),
                "new_weight": float(proposal.new_weight),
                "review_notes": proposal.review_notes,
            },
            proposal=proposal,
        )

    def rollback(self, update_id: str) -> None:
        clean_id = str(update_id or "").strip()
        if not clean_id:
            raise ValueError("update_id must be non-empty")
        proposal = self._applied_proposals.get(clean_id)
        if proposal is None:
            raise KeyError(f"unknown weight update id: {clean_id}")
        _set_weight(self.consensus_engine_ref, proposal.agent_role, float(proposal.old_weight))
        proposal.rolled_back_at = _utc_now_iso()
        self._log_event(
            subtype="weight_update_rolled_back",
            payload={
                "proposal_id": proposal.id,
                "agent_role": proposal.agent_role,
                "restored_weight": float(proposal.old_weight),
            },
            proposal=proposal,
        )

    def _review_with_council(self, proposal: WeightUpdateProposal) -> tuple[bool, str]:
        candidates = []
        for owner in (self.consensus_engine_ref, getattr(self.consensus_engine_ref, "council_ref", None)):
            if owner is None:
                continue
            for method_name in ("review_weight_update", "review_update", "review_proposal", "approve_weight_update"):
                method = getattr(owner, method_name, None)
                if callable(method):
                    candidates.append(method)

        for method in candidates:
            try:
                out = method(proposal=proposal)
            except TypeError:
                out = method(proposal)
            out = _resolve_awaitable(out)
            approved, notes = _parse_review_result(out)
            if approved:
                return True, notes
            return False, notes
        return False, "council_review_unavailable"

    def _log_event(self, *, subtype: str, payload: dict[str, Any], proposal: WeightUpdateProposal) -> None:
        save = getattr(self.memory_ref, "save", None)
        if not callable(save):
            return
        project_id = _project_id_from_proposal(proposal=proposal, fallback_source=self.consensus_engine_ref)
        domain = _domain_from_proposal(proposal=proposal, fallback_source=self.consensus_engine_ref)
        save(
            project_id,
            domain,
            "event",
            {
                "subtype": str(subtype),
                "project_id": project_id,
                "domain": domain,
                "payload": dict(payload),
                "created_at": _utc_now_iso(),
            },
        )


def _compute_delta(pattern: Pattern) -> float:
    signal = (float(pattern.hit_rate) - 0.5) * 2.0
    confidence = max(0.0, min(1.0, float(pattern.confidence)))
    sample_factor = min(1.0, float(pattern.sample_size) / 200.0)
    delta = signal * confidence * sample_factor * 0.25
    return max(-0.35, min(0.35, delta))


def _role_from_pattern(pattern: Pattern) -> str:
    feature = dict(pattern.feature_combo)
    role = str(feature.get("agent_role") or feature.get("role") or "domain_expert").strip()
    return role or "domain_expert"


def _justification(*, pattern: Pattern, delta: float) -> str:
    direction = "increase" if delta >= 0 else "decrease"
    return (
        f"{direction}_weight based on pattern outcome={pattern.outcome}, "
        f"hit_rate={pattern.hit_rate:.3f}, confidence={pattern.confidence:.3f}, "
        f"sample_size={pattern.sample_size}"
    )


def _get_weight(engine: Any, role: str) -> float:
    getter = getattr(engine, "get_weight", None)
    if callable(getter):
        try:
            return float(getter(role))
        except Exception:
            pass
    weights = getattr(engine, "role_weights", None)
    if isinstance(weights, dict):
        return float(weights.get(str(role), 1.0))
    return 1.0


def _set_weight(engine: Any, role: str, value: float) -> None:
    setter = getattr(engine, "set_weight", None)
    if callable(setter):
        setter(role, float(value))
        return
    weights = getattr(engine, "role_weights", None)
    if isinstance(weights, dict):
        weights[str(role)] = float(value)
        return
    raise RuntimeError("consensus engine does not support weight updates")


def _parse_review_result(value: Any) -> tuple[bool, str]:
    if isinstance(value, bool):
        return bool(value), "approved" if value else "rejected"
    if isinstance(value, dict):
        approved = bool(value.get("approved", False))
        notes = str(value.get("notes") or value.get("reason") or ("approved" if approved else "rejected"))
        return approved, notes
    return bool(value), "approved" if bool(value) else "rejected"


def _resolve_awaitable(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return asyncio.run(value)
    return value


def _project_id_from_proposal(*, proposal: WeightUpdateProposal, fallback_source: Any) -> str:
    from_pattern = proposal.pattern_ref.get("project_id")
    clean = str(from_pattern or "").strip()
    if clean:
        return clean
    feature_combo = proposal.pattern_ref.get("feature_combo")
    if isinstance(feature_combo, dict):
        clean = str(feature_combo.get("project_id") or "").strip()
        if clean:
            return clean
    for attr in ("project_id",):
        value = str(getattr(fallback_source, attr, "")).strip()
        if value:
            return value
    return "global"


def _domain_from_proposal(*, proposal: WeightUpdateProposal, fallback_source: Any) -> str:
    feature_combo = proposal.pattern_ref.get("feature_combo")
    if isinstance(feature_combo, dict):
        clean = str(feature_combo.get("domain") or "").strip()
        if clean:
            return clean
    for attr in ("domain",):
        value = str(getattr(fallback_source, attr, "")).strip()
        if value:
            return value
    return "learning"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
