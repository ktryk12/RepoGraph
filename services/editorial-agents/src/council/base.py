from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from babyai.council.proposal import Proposal


_ROLE_DEFAULT_WEIGHTS: dict[str, float] = {
    "context_agent": 0.60,
    "evidence_agent": 0.80,
    "domain_expert": 0.85,
    "risk_policy_agent": 0.90,
    "counterargument_agent": 0.70,
    "historian_agent": 0.65,
    "planner_agent": 0.75,
    "evaluator_agent": 0.88,
}


class Agent:
    def __init__(self, role: str, profile_config: Any, memory_ref: Any) -> None:
        clean_role = str(role or "").strip()
        if not clean_role:
            raise ValueError("role must be non-empty")
        self.role = clean_role
        self.profile_config = profile_config
        self.memory_ref = memory_ref

    def observe(self) -> dict[str, Any]:
        domains = _profile_field(self.profile_config, "domains", [])
        rubric = _profile_field(self.profile_config, "eval_rubric", {})
        risk_thresholds = _profile_field(self.profile_config, "risk_thresholds", {})
        return {
            "role": self.role,
            "domains": list(domains) if isinstance(domains, list) else [],
            "risk_thresholds": dict(risk_thresholds) if isinstance(risk_thresholds, dict) else {},
            "rubric_dimensions": sorted(
                str(key) for key in (rubric.keys() if isinstance(rubric, dict) else []) if str(key).strip()
            ),
        }

    def propose(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "focus": self._role_focus(),
            "weight": self._role_weight(),
        }

    def deliberate(self, proposal: Proposal | dict[str, Any]) -> dict[str, Any]:
        payload = _proposal_to_dict(proposal)
        confidence = _as_float(payload.get("confidence"), default=0.0)
        base_score = max(0.0, min(1.0, confidence * self._role_weight()))
        assumptions = payload.get("assumptions")
        assumptions_count = len(assumptions) if isinstance(assumptions, list) else 0
        risks = self._default_risks(payload=payload, score=base_score)
        return {
            "role": self.role,
            "support_score": base_score,
            "assumptions_count": int(assumptions_count),
            "focus": self._role_focus(),
            "risks": risks,
            "constraints": self._default_constraints(),
            "rationale": f"{self.role} assessed proposal with score={base_score:.3f}",
        }

    def vote(self, deliberation: dict[str, Any]) -> dict[str, Any]:
        score = _as_float(deliberation.get("support_score"), default=0.0)
        recommendation = "approve" if score >= 0.5 else "reject"
        confidence = max(0.0, min(1.0, score))
        return {
            "role": self.role,
            "recommendation": recommendation,
            "confidence": confidence,
            "weight": self._role_weight(),
            "rationale": str(deliberation.get("rationale") or ""),
        }

    def _default_risks(self, *, payload: dict[str, Any], score: float) -> list[str]:
        risks: list[str] = []
        if self.role == "counterargument_agent":
            risks.append("counterfactual_breakage")
        if self.role == "risk_policy_agent":
            risks.append("policy_violation")
        assumptions = payload.get("assumptions")
        if isinstance(assumptions, list) and len(assumptions) > 2:
            risks.append("assumption_load_high")
        if score < 0.4:
            risks.append("low_support_signal")
        return sorted(set(risks))

    def _default_constraints(self) -> list[str]:
        risk_thresholds = _profile_field(self.profile_config, "risk_thresholds", {})
        if not isinstance(risk_thresholds, dict):
            return []
        out: list[str] = []
        for key, value in risk_thresholds.items():
            out.append(f"{key}<={value}")
        return sorted(out)

    def _role_focus(self) -> str:
        return {
            "context_agent": "background_and_trends",
            "evidence_agent": "data_quality_and_sources",
            "domain_expert": "domain_specific_validation",
            "risk_policy_agent": "constraints_and_compliance",
            "counterargument_agent": "failure_modes_and_counter_hypotheses",
            "historian_agent": "precedent_and_base_rates",
            "planner_agent": "options_and_tradeoffs",
            "evaluator_agent": "rubric_quality_and_scoring",
        }.get(self.role, "general_analysis")

    def _role_weight(self) -> float:
        return float(_ROLE_DEFAULT_WEIGHTS.get(self.role, 0.65))


def _profile_field(profile_config: Any, field_name: str, fallback: Any) -> Any:
    if profile_config is None:
        return fallback
    if isinstance(profile_config, dict):
        return profile_config.get(field_name, fallback)
    if is_dataclass(profile_config):
        payload = asdict(profile_config)
        return payload.get(field_name, fallback)
    value = getattr(profile_config, field_name, fallback)
    return value


def _proposal_to_dict(proposal: Proposal | dict[str, Any]) -> dict[str, Any]:
    if isinstance(proposal, Proposal):
        return asdict(proposal)
    if isinstance(proposal, dict):
        return dict(proposal)
    return {}


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)
