from __future__ import annotations

from typing import Any, Dict, List, Mapping

from pydantic import BaseModel, Field

from babyai.policy_consensus.conflict_detector import ConflictDetector
from babyai.policy_consensus.models import PolicyDirective
from babyai.policy_consensus.recursive_engine import RecursiveVerificationEngine

try:
    from fastapi import APIRouter
except Exception:  # pragma: no cover - optional dependency
    APIRouter = None  # type: ignore[assignment]


class ConsensusResolveRequest(BaseModel):
    policies: List[PolicyDirective] = Field(default_factory=list)
    request_context: str = ""
    effective_policy: Dict[str, Any] = Field(default_factory=dict)
    max_conflicts: int = 3


def create_router(
    *,
    detector: ConflictDetector,
    engine: RecursiveVerificationEngine,
) -> Any:
    if APIRouter is None:
        raise RuntimeError("FastAPI is required for consensus_router")
    router = APIRouter(prefix="/v1/policy-consensus", tags=["policy-consensus"])

    @router.post("/resolve")
    async def resolve(request: ConsensusResolveRequest) -> Dict[str, Any]:
        policies = list(request.policies or [])
        if not policies:
            policies = _policies_from_effective_policy(request.effective_policy)
        conflicts = await detector.scan(policies=policies, request_context=str(request.request_context or ""))
        limit = max(1, int(request.max_conflicts or 3))
        decisions = []
        for conflict in conflicts[:limit]:
            final = await engine.resolve(conflict)
            decisions.append(_model_dump(final))
        return {
            "conflict_count": len(conflicts),
            "evaluated_conflicts": len(decisions),
            "decisions": decisions,
        }

    return router


def _policies_from_effective_policy(payload: Mapping[str, Any]) -> List[PolicyDirective]:
    if not isinstance(payload, Mapping):
        return []
    domain = str(payload.get("domain_name") or "general")
    directives: List[PolicyDirective] = []
    actions = payload.get("autonomous_actions")
    if isinstance(actions, list):
        for idx, action in enumerate(actions):
            text = str(action or "").strip()
            if not text:
                continue
            directives.append(
                PolicyDirective(
                    policy_id=f"effective-{idx+1}",
                    domain=domain,
                    directive=text,
                    priority=5,
                    tags=["autonomous_action"],
                )
            )
    forbidden = payload.get("forbidden_outputs")
    if isinstance(forbidden, list):
        for idx, item in enumerate(forbidden):
            text = str(item or "").strip()
            if not text:
                continue
            directives.append(
                PolicyDirective(
                    policy_id=f"forbidden-{idx+1}",
                    domain=domain,
                    directive=f"Never output: {text}",
                    priority=9,
                    tags=["restriction"],
                )
            )
    return directives


def _model_dump(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return dict(model.model_dump())
    if hasattr(model, "dict"):
        return dict(model.dict())
    return {}

