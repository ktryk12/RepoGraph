from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import yaml


DEFAULT_RIGHTS_GUARD_PATH = Path(__file__).with_name("rights_guard.yaml")
VERDICT_ALLOW = "ALLOW"
VERDICT_REVIEW = "REVIEW"
VERDICT_DENY = "DENY"


@dataclass(frozen=True)
class RightsGuardPolicy:
    name: str
    allow_labels: frozenset[str] = field(default_factory=frozenset)
    review_labels: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class CandidateRightsVerdict:
    candidate_id: str
    source_ref: str
    rights_label: str
    verdict: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": str(self.candidate_id),
            "source_ref": str(self.source_ref),
            "rights_label": str(self.rights_label),
            "verdict": str(self.verdict),
            "reason": str(self.reason),
        }


@dataclass(frozen=True)
class RightsGuardDecision:
    policy_name: str
    overall_verdict: str
    candidate_verdicts: List[CandidateRightsVerdict]
    can_fetch: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_name": str(self.policy_name),
            "overall_verdict": str(self.overall_verdict),
            "can_fetch": bool(self.can_fetch),
            "candidate_verdicts": [item.to_dict() for item in self.candidate_verdicts],
        }


class RightsGuardService:
    def __init__(self, *, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_RIGHTS_GUARD_PATH
        self._default_policy, self._policies = load_rights_guard(self._path)

    def reload(self) -> None:
        self._default_policy, self._policies = load_rights_guard(self._path)

    def evaluate(
        self,
        *,
        candidates: Sequence[Mapping[str, Any]],
        policy_name: str | None,
    ) -> RightsGuardDecision:
        selected_policy_name = str(policy_name or "").strip() or self._default_policy
        policy = self._policies.get(selected_policy_name)
        if policy is None:
            return RightsGuardDecision(
                policy_name=selected_policy_name,
                overall_verdict=VERDICT_DENY,
                candidate_verdicts=[],
                can_fetch=False,
            )

        verdicts: List[CandidateRightsVerdict] = []
        for idx, item in enumerate(candidates):
            candidate_id = _non_empty(item.get("candidate_id")) or f"candidate-{idx + 1}"
            source_ref = _non_empty(item.get("source_ref"))
            rights_label = _non_empty(item.get("rights_label")).lower() or "unknown"
            if rights_label in policy.allow_labels:
                verdict = VERDICT_ALLOW
                reason = "rights_label_allowed"
            elif rights_label in policy.review_labels:
                verdict = VERDICT_REVIEW
                reason = "rights_label_requires_review"
            else:
                verdict = VERDICT_DENY
                reason = "rights_label_unknown_or_denied"
            verdicts.append(
                CandidateRightsVerdict(
                    candidate_id=candidate_id,
                    source_ref=source_ref,
                    rights_label=rights_label,
                    verdict=verdict,
                    reason=reason,
                )
            )

        overall = VERDICT_ALLOW
        if any(row.verdict == VERDICT_DENY for row in verdicts):
            overall = VERDICT_DENY
        elif any(row.verdict == VERDICT_REVIEW for row in verdicts):
            overall = VERDICT_REVIEW
        return RightsGuardDecision(
            policy_name=selected_policy_name,
            overall_verdict=overall,
            candidate_verdicts=verdicts,
            can_fetch=(overall in {VERDICT_ALLOW, VERDICT_REVIEW}),
        )


_RIGHTS_GUARD_SERVICE: RightsGuardService | None = None


def get_rights_guard_service(*, path: str | Path | None = None, reload: bool = False) -> RightsGuardService:
    global _RIGHTS_GUARD_SERVICE
    if _RIGHTS_GUARD_SERVICE is None or path is not None:
        _RIGHTS_GUARD_SERVICE = RightsGuardService(path=path)
        return _RIGHTS_GUARD_SERVICE
    if reload:
        _RIGHTS_GUARD_SERVICE.reload()
    return _RIGHTS_GUARD_SERVICE


def load_rights_guard(path: str | Path) -> tuple[str, Dict[str, RightsGuardPolicy]]:
    target = Path(path).resolve()
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        payload = {}

    policies_raw = payload.get("policies")
    if not isinstance(policies_raw, Mapping):
        policies_raw = {}
    policies: Dict[str, RightsGuardPolicy] = {}
    for raw_name, raw_cfg in policies_raw.items():
        name = str(raw_name or "").strip()
        if not name or not isinstance(raw_cfg, Mapping):
            continue
        allow = _normalize_labels(raw_cfg.get("allow"))
        review = _normalize_labels(raw_cfg.get("review"))
        policies[name] = RightsGuardPolicy(
            name=name,
            allow_labels=frozenset(allow),
            review_labels=frozenset(review),
        )

    default_policy = str(payload.get("default_policy") or "").strip()
    if not default_policy or default_policy not in policies:
        default_policy = next(iter(policies.keys()), "standard")
        if default_policy not in policies:
            policies[default_policy] = RightsGuardPolicy(name=default_policy, allow_labels=frozenset(), review_labels=frozenset())
    return default_policy, policies


def _normalize_labels(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for item in value:
        label = str(item or "").strip().lower()
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def _non_empty(value: Any) -> str:
    return str(value or "").strip()
