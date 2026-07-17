from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping

from babyai_shared.court.contracts import HearingRequest
from babyai_shared.court.engine import CourtService
from babyai_shared.fingerprint import sha256_json
from babyai_shared.ops.killswitch import KillSwitchService, get_killswitch_service
from policy.case_service import CaseService, get_case_service
from policy.constitution_service import ConstitutionService, get_constitution_service
from babyai_shared.storage.safe_paths import safe_segment


DEFAULT_POLICY_ADOPTION_ARTIFACT_ROOT = Path("artifacts") / "policy_adoption"


@dataclass(frozen=True)
class HumanSignoff:
    approved: bool = False
    signed_by: str | None = None
    ticket_ref: str | None = None
    signed_at: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "approved", bool(self.approved))
        object.__setattr__(self, "signed_by", _optional_text(self.signed_by))
        object.__setattr__(self, "ticket_ref", _optional_text(self.ticket_ref))
        object.__setattr__(self, "signed_at", _optional_text(self.signed_at))
        object.__setattr__(self, "metadata", _as_dict(self.metadata))

    @property
    def valid(self) -> bool:
        return bool(self.approved and _optional_text(self.signed_by))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "approved": bool(self.approved),
            "signed_by": self.signed_by,
            "ticket_ref": self.ticket_ref,
            "signed_at": self.signed_at,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any] | None) -> "HumanSignoff":
        if not isinstance(payload, Mapping):
            return cls()
        return cls(
            approved=bool(payload.get("approved", False)),
            signed_by=_optional_text(payload.get("signed_by")),
            ticket_ref=_optional_text(payload.get("ticket_ref")),
            signed_at=_optional_text(payload.get("signed_at")),
            metadata=_as_dict(payload.get("metadata")),
        )


@dataclass(frozen=True)
class PolicyAdoptionRequest:
    proposal: Dict[str, Any]
    review_report: Dict[str, Any]
    judge_pack: Dict[str, Any]
    requested_action: str = "adopt"
    case_id: str | None = None
    run_id: str | None = None
    subject_id: str | None = None
    policy_scope: str | None = None
    human_signoff: HumanSignoff = field(default_factory=HumanSignoff)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal", _as_dict(self.proposal))
        object.__setattr__(self, "review_report", _as_dict(self.review_report))
        object.__setattr__(self, "judge_pack", _as_dict(self.judge_pack))
        object.__setattr__(self, "requested_action", _normalize_action(self.requested_action))
        object.__setattr__(self, "case_id", _optional_text(self.case_id))
        object.__setattr__(self, "run_id", _optional_text(self.run_id))
        object.__setattr__(self, "subject_id", _optional_text(self.subject_id))
        object.__setattr__(self, "policy_scope", _normalize_scope(self.policy_scope))
        object.__setattr__(self, "metadata", _as_dict(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal": dict(self.proposal),
            "review_report": dict(self.review_report),
            "judge_pack": dict(self.judge_pack),
            "requested_action": self.requested_action,
            "case_id": self.case_id,
            "run_id": self.run_id,
            "subject_id": self.subject_id,
            "policy_scope": self.policy_scope,
            "human_signoff": self.human_signoff.to_dict(),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "PolicyAdoptionRequest":
        if not isinstance(payload, Mapping):
            raise ValueError("policy adoption request must be an object")
        return cls(
            proposal=_as_dict(payload.get("proposal")),
            review_report=_as_dict(payload.get("review_report")),
            judge_pack=_as_dict(payload.get("judge_pack")),
            requested_action=_normalize_action(payload.get("requested_action")),
            case_id=_optional_text(payload.get("case_id")),
            run_id=_optional_text(payload.get("run_id")),
            subject_id=_optional_text(payload.get("subject_id")),
            policy_scope=_normalize_scope(payload.get("policy_scope")),
            human_signoff=HumanSignoff.from_dict(_as_mapping(payload.get("human_signoff"))),
            metadata=_as_dict(payload.get("metadata")),
        )


@dataclass(frozen=True)
class PolicyAdoptionResult:
    adoption_id: str
    allowed: bool
    requested_action: str
    policy_scope: str
    case_id: str
    run_id: str | None
    subject_id: str | None
    proposal_fingerprint: str
    court_hard_pass: bool
    court_allowed: bool
    requires_human_signoff: bool
    human_signoff_valid: bool
    conflict_tags: List[str]
    reasons: List[str]
    write_count: int
    artifact_ref: str | None
    constitution_verdict: Dict[str, Any]
    metadata: Dict[str, Any]
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "adoption_id", _required_text(self.adoption_id, name="adoption_id"))
        object.__setattr__(self, "requested_action", _normalize_action(self.requested_action))
        object.__setattr__(self, "policy_scope", _required_text(self.policy_scope, name="policy_scope"))
        object.__setattr__(self, "case_id", _required_text(self.case_id, name="case_id"))
        object.__setattr__(self, "run_id", _optional_text(self.run_id))
        object.__setattr__(self, "subject_id", _optional_text(self.subject_id))
        object.__setattr__(self, "proposal_fingerprint", _required_text(self.proposal_fingerprint, name="proposal_fingerprint"))
        object.__setattr__(self, "court_hard_pass", bool(self.court_hard_pass))
        object.__setattr__(self, "court_allowed", bool(self.court_allowed))
        object.__setattr__(self, "requires_human_signoff", bool(self.requires_human_signoff))
        object.__setattr__(self, "human_signoff_valid", bool(self.human_signoff_valid))
        object.__setattr__(self, "allowed", bool(self.allowed))
        object.__setattr__(self, "conflict_tags", _normalize_strings(self.conflict_tags))
        object.__setattr__(self, "reasons", _normalize_strings(self.reasons))
        write_count = int(self.write_count)
        if write_count < 0:
            raise ValueError("write_count must be >= 0")
        object.__setattr__(self, "write_count", write_count)
        object.__setattr__(self, "artifact_ref", _optional_text(self.artifact_ref))
        object.__setattr__(self, "constitution_verdict", _as_dict(self.constitution_verdict))
        object.__setattr__(self, "metadata", _as_dict(self.metadata))
        object.__setattr__(self, "fingerprint", _required_text(self.fingerprint, name="fingerprint"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "adoption_id": self.adoption_id,
            "allowed": bool(self.allowed),
            "requested_action": self.requested_action,
            "policy_scope": self.policy_scope,
            "case_id": self.case_id,
            "run_id": self.run_id,
            "subject_id": self.subject_id,
            "proposal_fingerprint": self.proposal_fingerprint,
            "court_hard_pass": bool(self.court_hard_pass),
            "court_allowed": bool(self.court_allowed),
            "requires_human_signoff": bool(self.requires_human_signoff),
            "human_signoff_valid": bool(self.human_signoff_valid),
            "conflict_tags": list(self.conflict_tags),
            "reasons": list(self.reasons),
            "write_count": int(self.write_count),
            "artifact_ref": self.artifact_ref,
            "constitution_verdict": dict(self.constitution_verdict),
            "metadata": dict(self.metadata),
            "fingerprint": self.fingerprint,
        }


class PolicyAdoptionService:
    """
    Adoption gate for policy proposals using court + judge signals.

    Rules:
    - Case policy can auto-adopt when gates pass.
    - Global policy requires explicit human signoff.
    - Writes are bounded by max_writes per call (default: 1).
    """

    def __init__(
        self,
        *,
        court: CourtService | None = None,
        case_service: CaseService | None = None,
        constitution: ConstitutionService | None = None,
        killswitch: KillSwitchService | None = None,
        artifact_root: str | Path = DEFAULT_POLICY_ADOPTION_ARTIFACT_ROOT,
    ) -> None:
        self._court = court or CourtService(allow_artifact_writes=False)
        self._case_service = case_service or get_case_service()
        self._constitution = constitution or get_constitution_service()
        self._killswitch = killswitch or get_killswitch_service()
        self._artifact_root = Path(artifact_root)
        from verify.artifacts.registry import ArtifactRegistry
        self._registry = ArtifactRegistry(manifest_path=self._artifact_root / "manifest.jsonl")

    def evaluate(self, request: PolicyAdoptionRequest | Mapping[str, Any]) -> PolicyAdoptionResult:
        req = _to_request(request)
        resolved_case = self._resolve_case_id(req)
        scope = req.policy_scope or ("global" if self._case_service.is_default_case(resolved_case) else "case")
        proposal_fingerprint = sha256_json(req.proposal)

        hearing = self._build_hearing(req, case_id=resolved_case)
        court_result = self._court.hear(hearing, persist=False)
        recommendation = _as_dict((court_result.metadata or {}).get("recommendation"))
        court_allowed = bool(recommendation.get("allowed", court_result.hard_pass))

        conflict_tags = _detect_conflicts(req.proposal)
        requires_human_signoff = bool(scope == "global" and req.requested_action != "review")
        human_signoff_valid = bool(req.human_signoff.valid)
        reasons: List[str] = []
        if not court_allowed:
            blocked = _normalize_strings(recommendation.get("blocked_reasons"))
            if not blocked:
                blocked = ["court_rejected"]
            for tag in blocked:
                reasons.append(f"court_blocked:{tag}")
        if conflict_tags:
            reasons.append("policy_conflicts_detected")
        if requires_human_signoff and not human_signoff_valid:
            reasons.append("global_policy_requires_human_signoff")

        decision_payload = {
            "decision_id": _decision_id(req, proposal_fingerprint=proposal_fingerprint, case_id=resolved_case, scope=scope),
            "action": req.requested_action,
            "case_id": resolved_case,
            "subject_id": req.subject_id,
            "proposal_fingerprint": proposal_fingerprint,
            "court_allowed": bool(court_allowed),
            "conflict_tags": list(conflict_tags),
            "human_signoff_valid": bool(human_signoff_valid),
            "policy_scope": scope,
        }
        constitution_verdict = self._constitution.validate(
            "decision_provenance",
            {
                "decision": decision_payload,
                "context_id": resolved_case,
            },
        )
        if not constitution_verdict.allowed:
            reasons.append(f"constitution:{constitution_verdict.rule_id or 'decision_provenance'}")

        allowed = bool(court_allowed and not conflict_tags and (not requires_human_signoff or human_signoff_valid))
        if not constitution_verdict.allowed:
            allowed = False
        adoption_id = _adoption_id(
            proposal_fingerprint=proposal_fingerprint,
            case_id=resolved_case,
            requested_action=req.requested_action,
            subject_id=req.subject_id,
            run_id=req.run_id,
        )

        metadata = {
            "source_ref": "policy.policy_adoption_service",
            "recommendation": recommendation,
            "human_signoff": req.human_signoff.to_dict(),
            "conflict_tags": list(conflict_tags),
            "write_mode": "read_only",
            "scope_resolution": {
                "case_id": resolved_case,
                "policy_scope": scope,
            },
            "court_result_fingerprint": sha256_json(court_result.to_dict()),
        }
        base_payload = {
            "adoption_id": adoption_id,
            "allowed": bool(allowed),
            "requested_action": req.requested_action,
            "policy_scope": scope,
            "case_id": resolved_case,
            "run_id": req.run_id,
            "subject_id": req.subject_id,
            "proposal_fingerprint": proposal_fingerprint,
            "court_hard_pass": bool(court_result.hard_pass),
            "court_allowed": bool(court_allowed),
            "requires_human_signoff": bool(requires_human_signoff),
            "human_signoff_valid": bool(human_signoff_valid),
            "conflict_tags": list(conflict_tags),
            "reasons": _normalize_strings(reasons),
            "write_count": 0,
            "artifact_ref": None,
            "constitution_verdict": constitution_verdict.to_dict(),
            "metadata": metadata,
        }
        fingerprint = sha256_json(base_payload)
        return PolicyAdoptionResult(
            **base_payload,
            fingerprint=fingerprint,
        )

    def adopt(
        self,
        request: PolicyAdoptionRequest | Mapping[str, Any],
        *,
        persist: bool = False,
        max_writes: int = 1,
    ) -> PolicyAdoptionResult:
        result = self.evaluate(request)
        if not persist:
            return result
        if max_writes < 1:
            raise ValueError("max_writes must be >= 1 when persist=True")
        if not result.allowed:
            return result

        self._killswitch.require_write(
            operation="policy.adoption.persist",
            scope="POLICY_ADOPT",
            context={"service": "policy", "case_id": result.case_id},
        )

        artifact_path = self._artifact_path(result)
        payload = result.to_dict()
        payload["metadata"] = {**_as_dict(payload.get("metadata")), "write_mode": "persist"}
        payload["write_count"] = 1
        payload["artifact_ref"] = artifact_path.as_posix()
        payload["fingerprint"] = sha256_json(
            {
                **payload,
                "artifact_ref": None,
                "fingerprint": None,
            }
        )
        from verify.artifacts.registry import write_artifact
        write_artifact(
            "policy_adoption_json",
            payload,
            artifact_path,
            metadata={
                "source_ref": "policy.policy_adoption_service",
                "case_id": result.case_id,
                "job_id": result.run_id,
            },
            registry=self._registry,
        )
        return PolicyAdoptionResult(
            adoption_id=result.adoption_id,
            allowed=bool(result.allowed),
            requested_action=result.requested_action,
            policy_scope=result.policy_scope,
            case_id=result.case_id,
            run_id=result.run_id,
            subject_id=result.subject_id,
            proposal_fingerprint=result.proposal_fingerprint,
            court_hard_pass=bool(result.court_hard_pass),
            court_allowed=bool(result.court_allowed),
            requires_human_signoff=bool(result.requires_human_signoff),
            human_signoff_valid=bool(result.human_signoff_valid),
            conflict_tags=list(result.conflict_tags),
            reasons=list(result.reasons),
            write_count=1,
            artifact_ref=artifact_path.as_posix(),
            constitution_verdict=dict(result.constitution_verdict),
            metadata={**dict(result.metadata), "write_mode": "persist"},
            fingerprint=str(payload["fingerprint"]),
        )

    def _resolve_case_id(self, request: PolicyAdoptionRequest) -> str:
        proposal_case = _optional_text(request.proposal.get("case_id"))
        namespace_case = _case_id_from_namespace(request.proposal.get("namespace"))
        return self._case_service.resolve_case_id(
            case_id=request.case_id or proposal_case or namespace_case,
            context={**request.proposal, **request.metadata},
        )

    def _build_hearing(self, request: PolicyAdoptionRequest, *, case_id: str) -> HearingRequest:
        return HearingRequest(
            review_report=request.review_report,
            judge_pack=request.judge_pack,
            requested_action=request.requested_action,
            run_id=request.run_id,
            case_id=case_id,
            subject_id=request.subject_id,
            metadata={
                "source": "policy.policy_adoption_service",
                **request.metadata,
            },
        )

    def _artifact_path(self, result: PolicyAdoptionResult) -> Path:
        scope_segment = "global" if result.policy_scope == "global" else f"case-{safe_segment(result.case_id)}"
        run_segment = safe_segment(result.run_id or "adhoc")
        file_name = f"{safe_segment(result.adoption_id)}.json"
        return self._artifact_root / scope_segment / run_segment / file_name


_SERVICE: PolicyAdoptionService | None = None


def get_policy_adoption_service(
    *,
    reload: bool = False,
    artifact_root: str | Path | None = None,
) -> PolicyAdoptionService:
    global _SERVICE
    if _SERVICE is None or reload or artifact_root is not None:
        _SERVICE = PolicyAdoptionService(
            artifact_root=(artifact_root if artifact_root is not None else DEFAULT_POLICY_ADOPTION_ARTIFACT_ROOT),
        )
    return _SERVICE


def _detect_conflicts(proposal: Mapping[str, Any]) -> List[str]:
    operations = _proposal_operations(proposal)
    seen: Dict[str, Dict[str, Any]] = {}
    tags: List[str] = []
    for operation in operations:
        target = operation["target"]
        current = seen.get(target)
        if current is None:
            seen[target] = operation
            continue
        conflict = bool(current["fingerprint"] != operation["fingerprint"])
        if not conflict:
            conflict = _ops_conflict(str(current.get("op") or ""), str(operation.get("op") or ""))
        if not conflict:
            continue
        tags.append(f"policy_conflict:{safe_segment(target)}")
    return _normalize_strings(tags)


def _proposal_operations(proposal: Mapping[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not isinstance(proposal, Mapping):
        return out
    if isinstance(proposal.get("changes"), list):
        for idx, row in enumerate(proposal["changes"]):
            if not isinstance(row, Mapping):
                continue
            target = (
                _optional_text(row.get("path"))
                or _optional_text(row.get("rule_id"))
                or _optional_text(row.get("target"))
                or _optional_text(row.get("change_id"))
                or f"change[{idx}]"
            )
            op = (_optional_text(row.get("op")) or "upsert").lower()
            out.append(
                {
                    "target": target,
                    "op": op,
                    "fingerprint": sha256_json(dict(row)),
                }
            )
        return out
    if isinstance(proposal.get("rules"), list):
        for idx, row in enumerate(proposal["rules"]):
            if not isinstance(row, Mapping):
                continue
            target = _optional_text(row.get("rule_id")) or f"rule[{idx}]"
            out.append(
                {
                    "target": target,
                    "op": "upsert",
                    "fingerprint": sha256_json(dict(row)),
                }
            )
    return out


def _ops_conflict(left: str, right: str) -> bool:
    if left == right:
        return False
    delete_ops = {"delete", "remove"}
    if left in delete_ops or right in delete_ops:
        return True
    return False


def _decision_id(
    request: PolicyAdoptionRequest,
    *,
    proposal_fingerprint: str,
    case_id: str,
    scope: str,
) -> str:
    seed = {
        "proposal_fingerprint": proposal_fingerprint,
        "case_id": case_id,
        "scope": scope,
        "requested_action": request.requested_action,
        "subject_id": request.subject_id,
        "run_id": request.run_id,
    }
    return f"policy-adoption-decision-{sha256_json(seed)[:16]}"


def _adoption_id(
    *,
    proposal_fingerprint: str,
    case_id: str,
    requested_action: str,
    subject_id: str | None,
    run_id: str | None,
) -> str:
    seed = {
        "proposal_fingerprint": proposal_fingerprint,
        "case_id": case_id,
        "requested_action": requested_action,
        "subject_id": subject_id,
        "run_id": run_id,
    }
    return f"policy-adoption-{sha256_json(seed)[:16]}"


def _case_id_from_namespace(value: Any) -> str | None:
    token = _optional_text(value)
    if not token:
        return None
    normalized = token.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if len(parts) >= 2 and parts[0] == "case" and parts[1]:
        return parts[1]
    return None


def _to_request(request: PolicyAdoptionRequest | Mapping[str, Any]) -> PolicyAdoptionRequest:
    if isinstance(request, PolicyAdoptionRequest):
        return request
    if not isinstance(request, Mapping):
        raise ValueError("policy adoption request must be an object")
    return PolicyAdoptionRequest(
        proposal=_as_dict(request.get("proposal")),
        review_report=_as_dict(request.get("review_report")),
        judge_pack=_as_dict(request.get("judge_pack")),
        requested_action=_normalize_action(request.get("requested_action")),
        case_id=_optional_text(request.get("case_id")),
        run_id=_optional_text(request.get("run_id")),
        subject_id=_optional_text(request.get("subject_id")),
        policy_scope=_normalize_scope(request.get("policy_scope")),
        human_signoff=HumanSignoff.from_dict(_as_mapping(request.get("human_signoff"))),
        metadata=_as_dict(request.get("metadata")),
    )


def _normalize_action(value: Any) -> str:
    text = str(value or "adopt").strip().lower()
    return text or "adopt"


def _normalize_scope(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    normalized = text.lower()
    if normalized not in {"case", "global"}:
        raise ValueError("policy_scope must be one of: case, global")
    return normalized


def _required_text(value: Any, *, name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return {}


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _normalize_strings(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    seen = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return sorted(out)
