from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class PerformanceReport:
    agent_id: str
    role: str
    signal_value: float
    calibration: float
    overlap: float
    policy_hygiene: float
    cost: float
    created_at: str


class PerformanceMonitor:
    def __init__(self, project_id: str, memory_ref: Any) -> None:
        self.project_id = str(project_id or "").strip()
        if not self.project_id:
            raise ValueError("project_id must be non-empty")
        self.memory_ref = memory_ref
        self._reports_by_agent_id: dict[str, PerformanceReport] = {}

    def evaluate(self, agent: Any) -> PerformanceReport:
        role = str(getattr(agent, "role", "")).strip() or "unknown"
        agent_id = _agent_id(agent)
        signal_value = _metric_signal(role=role)
        calibration = _metric_calibration(agent=agent)
        overlap = _metric_overlap(role=role)
        policy_hygiene = _metric_policy_hygiene(role=role, agent=agent)
        cost = _metric_cost(role=role)
        report = PerformanceReport(
            agent_id=agent_id,
            role=role,
            signal_value=signal_value,
            calibration=calibration,
            overlap=overlap,
            policy_hygiene=policy_hygiene,
            cost=cost,
            created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        self._reports_by_agent_id[agent_id] = report
        self._log_event(
            domain=_domain_from_agent(agent),
            event_name="agent_performance_evaluated",
            payload=report.__dict__,
        )
        return report

    def should_retire(self, agent: Any) -> bool:
        agent_id = _agent_id(agent)
        report = self._reports_by_agent_id.get(agent_id)
        if report is None:
            report = self.evaluate(agent)
        if report.signal_value < 0.25:
            return True
        if report.policy_hygiene < 0.30:
            return True
        if report.cost > 0.92 and report.signal_value < 0.55:
            return True
        return False

    def retire(self, agent: Any, council: Any, reason: str) -> None:
        roster = list(getattr(council, "agent_roster", []) or [])
        target = agent
        if target in roster:
            roster.remove(target)
            setattr(council, "agent_roster", roster)
        agent_id = _agent_id(agent)
        self._reports_by_agent_id.pop(agent_id, None)
        self._log_event(
            domain=str(getattr(council, "domain", "recruiter")),
            event_name="agent_retired",
            payload={
                "agent_id": agent_id,
                "role": str(getattr(agent, "role", "")),
                "reason": str(reason or "retired"),
                "council_id": str(getattr(council, "council_id", "")),
            },
        )

    def _log_event(self, *, domain: str, event_name: str, payload: dict[str, Any]) -> None:
        save = getattr(self.memory_ref, "save", None)
        if not callable(save):
            return
        save(
            self.project_id,
            str(domain or "recruiter"),
            "event",
            {
                "recruiter_event": str(event_name),
                "project_id": self.project_id,
                "domain": str(domain or "recruiter"),
                "payload": dict(payload),
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
        )


def _agent_id(agent: Any) -> str:
    existing = getattr(agent, "agent_id", None)
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    role = str(getattr(agent, "role", "agent")).strip() or "agent"
    generated = f"{role}:{id(agent)}"
    setattr(agent, "agent_id", generated)
    return generated


def _domain_from_agent(agent: Any) -> str:
    profile = getattr(agent, "profile_config", None)
    if isinstance(profile, dict):
        domains = profile.get("domains")
        if isinstance(domains, list) and domains:
            candidate = str(domains[0]).strip()
            if candidate:
                return candidate
    return "recruiter"


def _metric_signal(*, role: str) -> float:
    table = {
        "context_agent": 0.70,
        "evidence_agent": 0.78,
        "domain_expert": 0.80,
        "risk_policy_agent": 0.74,
        "counterargument_agent": 0.62,
        "historian_agent": 0.58,
        "planner_agent": 0.72,
        "evaluator_agent": 0.82,
        "recruiter_agent": 0.65,
    }
    return float(table.get(role, 0.50))


def _metric_calibration(*, agent: Any) -> float:
    propose = getattr(agent, "propose", None)
    if callable(propose):
        row = propose()
        if isinstance(row, dict):
            weight = row.get("weight")
            try:
                return max(0.0, min(1.0, float(weight)))
            except Exception:
                pass
    return 0.50


def _metric_overlap(*, role: str) -> float:
    if role == "domain_expert":
        return 0.60
    if role in {"planner_agent", "evaluator_agent"}:
        return 0.35
    return 0.25


def _metric_policy_hygiene(*, role: str, agent: Any) -> float:
    base = 0.70 if role != "risk_policy_agent" else 0.90
    profile = getattr(agent, "profile_config", None)
    if isinstance(profile, dict):
        permissions = profile.get("permissions", [])
        if isinstance(permissions, list) and permissions:
            base = min(1.0, base + 0.08)
    return float(base)


def _metric_cost(*, role: str) -> float:
    table = {
        "domain_expert": 0.78,
        "evidence_agent": 0.65,
        "risk_policy_agent": 0.60,
        "planner_agent": 0.55,
        "evaluator_agent": 0.62,
        "recruiter_agent": 0.50,
    }
    return float(table.get(role, 0.45))
