from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from babyai.council.base import Agent
from babyai.recruiter.agent_factory import AgentFactory
from babyai.recruiter.gap_detector import GapDetector
from babyai.recruiter.performance_monitor import PerformanceMonitor


class RecruiterAgent:
    def __init__(
        self,
        project_id: str,
        council_graph: Any,
        memory_ref: Any,
        *,
        gap_detector: GapDetector | None = None,
        agent_factory: AgentFactory | None = None,
        performance_monitor: PerformanceMonitor | None = None,
        tool_registry: Any | None = None,
        risk_policy: Any | None = None,
    ) -> None:
        self.project_id = str(project_id or "").strip()
        if not self.project_id:
            raise ValueError("project_id must be non-empty")
        self.council_graph = council_graph
        self.memory_ref = memory_ref
        self.gap_detector = gap_detector or GapDetector(self.project_id, self.memory_ref, "babyai:lora_gaps")
        self.agent_factory = agent_factory or AgentFactory()
        self.performance_monitor = performance_monitor or PerformanceMonitor(self.project_id, self.memory_ref)
        self.tool_registry = tool_registry if tool_registry is not None else {"*": ["default_reasoner"]}
        self.risk_policy = risk_policy if risk_policy is not None else {
            "low": ["read_memory"],
            "medium": ["read_memory", "write_memory"],
            "high": ["read_memory", "write_memory", "delegate"],
        }

    async def run(self, *, iterations: int | None = 1, poll_interval_seconds: float = 0.0) -> None:
        loop_count = 0
        while True:
            councils = _resolve_councils(self.council_graph)
            self._log_event(
                domain="recruiter",
                event_name="recruiter_cycle_started",
                payload={"iteration": loop_count + 1, "council_count": len(councils)},
            )
            for council in councils:
                gaps = self.gap_detector.detect_capability_gaps(council)
                for gap in gaps:
                    if str(gap.severity) not in {"medium", "high"}:
                        continue
                    profile = self.agent_factory.build_profile(gap)
                    profile = self.agent_factory.assign_tools(profile, self.tool_registry)
                    profile = self.agent_factory.assign_permissions(profile, self.risk_policy)
                    onboarded = self.agent_factory.onboard(profile, council)
                    self._log_event(
                        domain=str(getattr(council, "domain", "recruiter")),
                        event_name="recruitment_completed",
                        payload={
                            "council_id": str(getattr(council, "council_id", "")),
                            "agent_role": str(onboarded.role),
                            "profile_id": str(profile.id),
                            "gap_severity": str(gap.severity),
                        },
                    )

                for agent in list(getattr(council, "agent_roster", []) or []):
                    report = self.performance_monitor.evaluate(agent)
                    if self.performance_monitor.should_retire(agent):
                        self.performance_monitor.retire(agent, council, reason="low_performance")
                        self._log_event(
                            domain=str(getattr(council, "domain", "recruiter")),
                            event_name="retirement_completed",
                            payload={
                                "council_id": str(getattr(council, "council_id", "")),
                                "agent_id": str(report.agent_id),
                                "agent_role": str(report.role),
                                "reason": "low_performance",
                            },
                        )

            self._log_event(
                domain="recruiter",
                event_name="recruiter_cycle_completed",
                payload={"iteration": loop_count + 1, "council_count": len(councils)},
            )
            loop_count += 1
            if iterations is not None and loop_count >= int(iterations):
                return
            await asyncio.sleep(max(0.0, float(poll_interval_seconds)))

    def as_agent(self, profile_config: dict[str, Any] | None = None) -> Agent:
        config = dict(profile_config or {})
        config.setdefault("domains", ["meta"])
        config.setdefault("agent_roster", ["recruiter_agent"])
        config.setdefault("tool_bindings", {"recruiter_agent": ["gap_detection", "agent_factory", "monitoring"]})
        config.setdefault("risk_thresholds", {"max_recruitments_per_cycle": 10})
        config.setdefault("eval_rubric", {"coverage": 0.5, "stability": 0.5})
        return Agent(role="recruiter_agent", profile_config=config, memory_ref=self.memory_ref)

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


def _resolve_councils(council_graph: Any) -> list[Any]:
    accessor = getattr(council_graph, "councils", None)
    if callable(accessor):
        rows = accessor()
        if isinstance(rows, list):
            return list(rows)
    nodes_accessor = getattr(council_graph, "nodes", None)
    if callable(nodes_accessor):
        rows = nodes_accessor()
        if isinstance(rows, list):
            return [node.council for node in rows if hasattr(node, "council")]
    return []
