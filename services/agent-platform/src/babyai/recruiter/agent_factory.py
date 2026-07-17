from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from babyai.council.base import Agent
from babyai.recruiter.gap_detector import CapabilityGap


@dataclass
class AgentProfile:
    id: str
    domain: str
    role: str
    evidence: list[str]
    severity: str
    tool_bindings: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


class AgentFactory:
    def build_profile(self, gap: CapabilityGap) -> AgentProfile:
        return AgentProfile(
            id=str(uuid4()),
            domain=str(gap.domain),
            role=str(gap.missing_role),
            evidence=[str(item) for item in list(gap.evidence or [])],
            severity=str(gap.severity),
            metadata={"source": "gap_detector"},
        )

    def assign_tools(self, profile: AgentProfile, tool_registry: Any) -> AgentProfile:
        tools = _resolve_tools(tool_registry=tool_registry, role=profile.role, domain=profile.domain)
        profile.tool_bindings = sorted(set(tools))
        profile.metadata["tools_assigned"] = len(profile.tool_bindings)
        return profile

    def assign_permissions(self, profile: AgentProfile, risk_policy: Any) -> AgentProfile:
        permissions = _resolve_permissions(risk_policy=risk_policy, severity=profile.severity, role=profile.role)
        profile.permissions = sorted(set(permissions))
        profile.metadata["permissions_assigned"] = len(profile.permissions)
        return profile

    def onboard(self, profile: AgentProfile, council: Any) -> Agent:
        memory_ref = _memory_ref_from_council(council)
        profile_config = {
            "domains": [str(profile.domain)],
            "agent_roster": [str(agent.role) for agent in list(getattr(council, "agent_roster", []) or [])]
            + [str(profile.role)],
            "tool_bindings": {str(profile.role): list(profile.tool_bindings)},
            "risk_thresholds": {"recruiter_severity": str(profile.severity)},
            "eval_rubric": {"coverage": 1.0},
            "permissions": list(profile.permissions),
            "profile_id": str(profile.id),
            "metadata": dict(profile.metadata),
        }
        agent = Agent(role=str(profile.role), profile_config=profile_config, memory_ref=memory_ref)
        getattr(council, "agent_roster").append(agent)

        log = getattr(council, "_log_event", None)
        if callable(log):
            log(
                event_name="agent_onboarded",
                payload={
                    "agent_role": str(profile.role),
                    "profile_id": str(profile.id),
                    "domain": str(profile.domain),
                    "tool_bindings": list(profile.tool_bindings),
                    "permissions": list(profile.permissions),
                },
            )
        return agent


def _resolve_tools(*, tool_registry: Any, role: str, domain: str) -> list[str]:
    if tool_registry is None:
        return []
    if isinstance(tool_registry, dict):
        direct = tool_registry.get(str(role))
        if isinstance(direct, list):
            return [str(item).strip() for item in direct if str(item).strip()]
        fallback = tool_registry.get("*")
        if isinstance(fallback, list):
            return [str(item).strip() for item in fallback if str(item).strip()]
        return []
    method = getattr(tool_registry, "get_tools", None)
    if callable(method):
        out = method(role=str(role), domain=str(domain))
        if isinstance(out, list):
            return [str(item).strip() for item in out if str(item).strip()]
    return []


def _resolve_permissions(*, risk_policy: Any, severity: str, role: str) -> list[str]:
    if risk_policy is None:
        return []
    if isinstance(risk_policy, dict):
        by_role = risk_policy.get(str(role))
        if isinstance(by_role, list):
            return [str(item).strip() for item in by_role if str(item).strip()]
        by_severity = risk_policy.get(str(severity))
        if isinstance(by_severity, list):
            return [str(item).strip() for item in by_severity if str(item).strip()]
        default = risk_policy.get("*")
        if isinstance(default, list):
            return [str(item).strip() for item in default if str(item).strip()]
        return []
    method = getattr(risk_policy, "permissions_for", None)
    if callable(method):
        out = method(role=str(role), severity=str(severity))
        if isinstance(out, list):
            return [str(item).strip() for item in out if str(item).strip()]
    return []


def _memory_ref_from_council(council: Any) -> Any | None:
    roster = list(getattr(council, "agent_roster", []) or [])
    for agent in roster:
        value = getattr(agent, "memory_ref", None)
        if value is not None:
            return value
    return None
