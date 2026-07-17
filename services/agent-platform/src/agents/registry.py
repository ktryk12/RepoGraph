"""
Agent registry for dynamic agent management.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from babyai_shared.bus.protocol import MessageType


@dataclass
class AgentSpec:
    """Declarative contract for an agent."""

    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)
    required_capabilities: List[str] = field(default_factory=list)
    eval_spec: Dict[str, Any] | None = None


@dataclass
class AgentMetadata:
    """Metadata about registered agent."""

    agent_id: str
    role: str
    accepts: Set[MessageType] = field(default_factory=set)
    spec: AgentSpec = field(default_factory=AgentSpec)
    status: str = "active"
    messages_processed: int = 0
    last_error: Optional[str] = None


@dataclass(frozen=True)
class DomainDAG:
    """Deterministic role ordering for a domain."""

    domain: str
    roles: List[str]


class AgentRegistry:
    """Central registry for all agents in the system."""

    def __init__(self) -> None:
        self._agents: Dict[str, Any] = {}
        self._metadata: Dict[str, AgentMetadata] = {}
        self._role_index: Dict[str, Set[str]] = {}
        self._message_type_index: Dict[MessageType, Set[str]] = {}
        self._domain_dags: Dict[str, DomainDAG] = {
            "software": DomainDAG(
                domain="software",
                roles=[
                    "supervisor",
                    "requirements",
                    "architect",
                    "validation",
                    "repair",
                    "translator",
                    "logger",
                ],
            ),
        }

    def register(self, agent: Any) -> None:
        """Register agent in system."""
        agent_id = agent.agent_id

        if agent_id in self._agents:
            raise ValueError(f"Agent {agent_id} already registered")

        self._agents[agent_id] = agent

        accepts_raw = getattr(agent, "accepts", None)
        accepts_set = set(accepts_raw) if accepts_raw else set()

        spec = self._build_spec(agent)
        metadata = AgentMetadata(
            agent_id=agent_id,
            role=agent.role,
            accepts=accepts_set,
            spec=spec,
        )
        self._metadata[agent_id] = metadata

        if agent.role not in self._role_index:
            self._role_index[agent.role] = set()
        self._role_index[agent.role].add(agent_id)

        if accepts_set:
            for msg_type in accepts_set:
                if msg_type not in self._message_type_index:
                    self._message_type_index[msg_type] = set()
                self._message_type_index[msg_type].add(agent_id)

    def unregister(self, agent_id: str) -> None:
        """Remove agent from registry."""
        if agent_id not in self._agents:
            return

        agent = self._agents[agent_id]
        metadata = self._metadata[agent_id]

        self._role_index.get(agent.role, set()).discard(agent_id)
        for msg_type in metadata.accepts:
            if msg_type in self._message_type_index:
                self._message_type_index[msg_type].discard(agent_id)

        del self._agents[agent_id]
        del self._metadata[agent_id]

    def get(self, agent_id: str) -> Optional[Any]:
        """Get agent by ID."""
        return self._agents.get(agent_id)

    def get_by_role(self, role: str) -> List[Any]:
        """Get all agents with specified role."""
        agent_ids = self._role_index.get(role, set())
        return [self._agents[aid] for aid in agent_ids]

    def find_handlers(self, message_type: MessageType) -> List[Any]:
        """Find all agents that can handle this message type."""
        handlers: List[Any] = []

        agent_ids = self._message_type_index.get(message_type, set())
        handlers.extend(self._agents[aid] for aid in agent_ids)

        for agent_id, metadata in self._metadata.items():
            if not metadata.accepts:
                agent = self._agents[agent_id]
                if agent.can_handle(message_type):
                    handlers.append(agent)

        return handlers

    def all(self) -> List[Any]:
        """Return all registered agents."""
        return list(self._agents.values())

    def mark_processed(self, agent_id: str) -> None:
        """Increment message counter."""
        if agent_id in self._metadata:
            self._metadata[agent_id].messages_processed += 1

    def mark_failed(self, agent_id: str, error: str) -> None:
        """Mark agent as failed."""
        if agent_id in self._metadata:
            self._metadata[agent_id].status = "failed"
            self._metadata[agent_id].last_error = error

    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        return {
            "total_agents": len(self._agents),
            "agents_by_role": {role: len(aids) for role, aids in self._role_index.items()},
            "active_agents": sum(1 for m in self._metadata.values() if m.status == "active"),
            "failed_agents": sum(1 for m in self._metadata.values() if m.status == "failed"),
        }

    def register_domain_dag(self, domain: str, roles: List[str]) -> None:
        self._domain_dags[domain] = DomainDAG(domain=domain, roles=list(roles))

    def get_domain_dag(self, domain: str) -> DomainDAG:
        if domain not in self._domain_dags:
            raise KeyError(f"Unknown domain DAG: {domain}")
        return self._domain_dags[domain]

    def select_team(self, domain: str) -> List[Any]:
        """
        Deterministically select one agent per role for a domain DAG.
        """
        dag = self.get_domain_dag(domain)
        selected: List[Any] = []
        for role in dag.roles:
            agents = self.get_by_role(role)
            if not agents:
                continue
            agents_sorted = sorted(agents, key=lambda a: a.agent_id)
            selected.append(agents_sorted[0])
        return selected

    @staticmethod
    def _build_spec(agent: Any) -> AgentSpec:
        spec = getattr(agent, "spec", None)
        if isinstance(spec, AgentSpec):
            return spec
        inputs = getattr(agent, "inputs", []) or []
        outputs = getattr(agent, "outputs", []) or []
        required = getattr(agent, "required_capabilities", []) or []
        eval_spec = getattr(agent, "eval_spec", None)
        return AgentSpec(
            inputs=list(inputs),
            outputs=list(outputs),
            required_capabilities=list(required),
            eval_spec=eval_spec if isinstance(eval_spec, dict) else None,
        )
