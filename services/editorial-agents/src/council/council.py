from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from babyai.council.base import Agent
from babyai.council.depth_controller import DepthController
from babyai.council.decision import Decision
from babyai.council.hierarchy import CouncilGraph, CouncilNode
from babyai.council.lateral import ConsultationResult, LateralBus
from babyai.council.proposal import Proposal
from babyai.policy_consensus.models import AgentType, AgentVote, Conflict, PolicyDirective


class Council:
    def __init__(
        self,
        project_id: str,
        domain: str,
        agent_roster: list[Agent],
        consensus_engine: Any,
        council_id: str | None = None,
        hierarchy_graph: CouncilGraph | None = None,
        lateral_bus: LateralBus | None = None,
        depth_controller: DepthController | None = None,
        current_depth: int = 0,
    ) -> None:
        clean_project_id = str(project_id or "").strip()
        clean_domain = str(domain or "").strip()
        if not clean_project_id:
            raise ValueError("project_id must be non-empty")
        if not clean_domain:
            raise ValueError("domain must be non-empty")
        if not list(agent_roster or []):
            raise ValueError("agent_roster must contain at least one agent")
        if consensus_engine is None:
            raise ValueError("consensus_engine must be provided")

        self.council_id = str(council_id or uuid4()).strip()
        if not self.council_id:
            raise ValueError("council_id must be non-empty")
        self.project_id = clean_project_id
        self.domain = clean_domain
        self.agent_roster = list(agent_roster)
        self.consensus_engine = consensus_engine
        self.hierarchy_graph = hierarchy_graph if hierarchy_graph is not None else CouncilGraph()
        self.lateral_bus = lateral_bus if lateral_bus is not None else LateralBus()
        self.depth_controller = depth_controller
        self.current_depth = max(0, int(current_depth))
        self._active_deliberation_session = "default"
        self._memory_ref = self._resolve_memory_ref()
        self._proposals: dict[str, Proposal] = {}
        self._deliberations: dict[str, dict[str, Any]] = {}
        self.hierarchy_graph.add_council(self)

    def run_mapping(self) -> list[dict[str, Any]]:
        knowledge_updates: list[dict[str, Any]] = []
        for agent in self.agent_roster:
            observation = agent.observe()
            knowledge_updates.append(
                {
                    "role": agent.role,
                    "observation": observation,
                    "observed_at": _utc_now_iso(),
                }
            )
        self._log_event(
            event_name="mapping_completed",
            payload={"knowledge_updates": knowledge_updates},
        )
        return knowledge_updates

    def submit_proposal(
        self,
        claim: str,
        evidence: list[Any],
        confidence: float,
        assumptions: list[str],
    ) -> str:
        clean_claim = str(claim or "").strip()
        if not clean_claim:
            raise ValueError("claim must be non-empty")
        proposal = Proposal(
            id=str(uuid4()),
            claim=clean_claim,
            evidence=list(evidence or []),
            confidence=max(0.0, min(1.0, float(confidence))),
            assumptions=[str(item).strip() for item in list(assumptions or []) if str(item).strip()],
            requested_decision="accept_or_reject",
            submitter_role="planner_agent",
            created_at=_utc_now_iso(),
        )
        self._proposals[proposal.id] = proposal
        self._log_event(event_name="proposal_submitted", payload={"proposal": asdict(proposal)})
        return proposal.id

    def run_deliberation(self, proposal_id: str) -> dict[str, Any]:
        clean_proposal_id = str(proposal_id or "").strip()
        proposal = self._proposals.get(clean_proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {clean_proposal_id}")

        rounds: list[dict[str, Any]] = []
        votes: list[dict[str, Any]] = []
        for agent in self.agent_roster:
            deliberation = agent.deliberate(proposal)
            vote = agent.vote(deliberation)
            rounds.append(
                {
                    "role": agent.role,
                    "deliberation": deliberation,
                    "vote": vote,
                }
            )
            votes.append(dict(vote))

        result = {
            "proposal_id": proposal.id,
            "domain": self.domain,
            "project_id": self.project_id,
            "deliberation_id": str(uuid4()),
            "agent_rounds": rounds,
            "votes": votes,
            "created_at": _utc_now_iso(),
        }
        self._active_deliberation_session = str(result["deliberation_id"])
        self._deliberations[result["deliberation_id"]] = result
        self._log_event(
            event_name="deliberation_completed",
            payload={
                "proposal_id": proposal.id,
                "deliberation_id": result["deliberation_id"],
                "votes": votes,
            },
        )
        return result

    def reach_decision(self, deliberation_result: dict[str, Any]) -> Decision:
        proposal_id = str(deliberation_result.get("proposal_id") or "").strip()
        if not proposal_id:
            raise ValueError("deliberation_result.proposal_id is required")
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise KeyError(f"proposal not found: {proposal_id}")
        votes = list(deliberation_result.get("votes") or [])
        engine_outcome = self._aggregate_votes(votes=votes, proposal=proposal)

        recommendation = str(engine_outcome.get("recommendation") or "reject").strip().lower()
        if recommendation not in {"approve", "reject"}:
            recommendation = "reject"
        decision_confidence = max(0.0, min(1.0, _as_float(engine_outcome.get("confidence"), default=0.0)))
        rationale = str(engine_outcome.get("rationale") or "").strip()

        risks = _collect_unique(deliberation_result, path=("agent_rounds", "deliberation", "risks"))
        constraints = _collect_unique(deliberation_result, path=("agent_rounds", "deliberation", "constraints"))

        decision = Decision(
            id=str(uuid4()),
            proposal_id=proposal.id,
            recommendation=recommendation,
            rationale=rationale or f"recommendation={recommendation}",
            confidence=decision_confidence,
            risks=risks,
            constraints=constraints,
            audit_trail=[
                {"event": "proposal_submitted", "proposal_id": proposal.id, "at": proposal.created_at},
                {
                    "event": "deliberation_completed",
                    "deliberation_id": str(deliberation_result.get("deliberation_id") or ""),
                    "at": str(deliberation_result.get("created_at") or _utc_now_iso()),
                },
                {
                    "event": "decision_reached",
                    "recommendation": recommendation,
                    "confidence": decision_confidence,
                    "at": _utc_now_iso(),
                },
            ],
            created_at=_utc_now_iso(),
        )
        self._log_event(event_name="decision_reached", payload={"decision": asdict(decision)})
        return decision

    def can_delegate(self) -> bool:
        if self.depth_controller is None:
            return True
        return bool(self.depth_controller.should_delegate(complexity_score=1.0, current_depth=self.current_depth))

    def spawn_subcouncil(self, question: str, profile: Any) -> CouncilNode:
        clean_question = str(question or "").strip()
        if not clean_question:
            raise ValueError("question must be non-empty")
        if not self.can_delegate():
            raise RuntimeError("delegation_not_allowed")

        config = _profile_to_config(profile)
        roster_roles = _profile_roster(profile)
        if not roster_roles:
            raise ValueError("profile must define a non-empty agent_roster")
        sub_domain = _profile_domain(profile, default=self.domain)
        sub_agents = [Agent(role=role, profile_config=config, memory_ref=self._memory_ref) for role in roster_roles]
        sub_council = Council(
            project_id=self.project_id,
            domain=sub_domain,
            agent_roster=sub_agents,
            consensus_engine=_spawn_consensus_engine(self.consensus_engine),
            hierarchy_graph=self.hierarchy_graph,
            lateral_bus=self.lateral_bus,
            depth_controller=self.depth_controller,
            current_depth=self.current_depth + 1,
        )
        node = self.hierarchy_graph.add_council(sub_council, parent=self)
        self._log_event(
            event_name="subcouncil_spawned",
            payload={
                "question": clean_question,
                "parent_council_id": self.council_id,
                "sub_council_id": sub_council.council_id,
                "sub_domain": sub_domain,
            },
        )
        return node

    def request_lateral(self, question: str, target_council_id: str) -> ConsultationResult:
        clean_target = str(target_council_id or "").strip()
        if not clean_target:
            raise ValueError("target_council_id must be non-empty")
        target_node = self.hierarchy_graph.get_node(clean_target)
        if target_node is None:
            raise KeyError(f"target council not found: {clean_target}")
        result = self.lateral_bus.consult(question=question, from_council=self, to_council=target_node.council)
        self._log_event(
            event_name="lateral_consultation_completed",
            payload={"target_council_id": clean_target, "question": str(question or ""), "result": result.__dict__},
        )
        return result

    def _answer_question(self, question: str, *, source: str, from_council_id: str | None = None) -> dict[str, Any]:
        proposal_id = self.submit_proposal(
            claim=str(question or ""),
            evidence=[{"source": str(source), "from_council_id": str(from_council_id or "")}],
            confidence=0.5,
            assumptions=[],
        )
        deliberation = self.run_deliberation(proposal_id)
        decision = self.reach_decision(deliberation)
        payload = {
            "council_id": self.council_id,
            "proposal_id": proposal_id,
            "recommendation": decision.recommendation,
            "rationale": decision.rationale,
            "confidence": float(decision.confidence),
            "source": str(source),
        }
        self._log_event(
            event_name="question_answered",
            payload={
                "question": str(question or ""),
                "source": str(source),
                "from_council_id": str(from_council_id or ""),
                "answer": payload,
            },
        )
        return payload

    def _aggregate_votes(self, *, votes: list[dict[str, Any]], proposal: Proposal) -> dict[str, Any]:
        aggregate = getattr(self.consensus_engine, "aggregate", None)
        if callable(aggregate):
            result = aggregate(votes=votes, proposal=proposal, domain=self.domain, project_id=self.project_id)
            if isinstance(result, dict):
                return result
            return {}

        resolve = getattr(self.consensus_engine, "resolve", None)
        if callable(resolve):
            return self._resolve_with_policy_consensus(votes=votes, proposal=proposal)

        approve_weight = 0.0
        reject_weight = 0.0
        for vote in votes:
            recommendation = str(vote.get("recommendation") or "").strip().lower()
            confidence = max(0.0, min(1.0, _as_float(vote.get("confidence"), default=0.0)))
            weight = max(0.0, _as_float(vote.get("weight"), default=1.0))
            signal = confidence * weight
            if recommendation == "approve":
                approve_weight += signal
            else:
                reject_weight += signal
        recommendation = "approve" if approve_weight >= reject_weight else "reject"
        total = approve_weight + reject_weight
        confidence = 0.0 if total <= 0 else max(approve_weight, reject_weight) / total
        return {
            "recommendation": recommendation,
            "confidence": confidence,
            "rationale": "fallback_weighted_vote",
        }

    def _resolve_with_policy_consensus(self, *, votes: list[dict[str, Any]], proposal: Proposal) -> dict[str, Any]:
        conflict = Conflict(
            policy_a=PolicyDirective(
                policy_id="approve",
                domain=self.domain,
                directive=f"Approve: {proposal.claim}",
                priority=5,
                tags=["council", "approve"],
            ),
            policy_b=PolicyDirective(
                policy_id="reject",
                domain=self.domain,
                directive=f"Reject: {proposal.claim}",
                priority=5,
                tags=["council", "reject"],
            ),
            dimension="council_deliberation",
            severity=max(0.0, min(1.0, 1.0 - float(proposal.confidence))),
            request_context=proposal.claim,
        )
        mapped_votes = [_to_agent_vote(vote, index=idx) for idx, vote in enumerate(votes)]
        policy_decision = self.consensus_engine.resolve(mapped_votes, conflict)
        winner_id = str(policy_decision.winning_policy.policy_id or "").strip().lower()
        recommendation = "approve" if winner_id == "approve" else "reject"
        return {
            "recommendation": recommendation,
            "confidence": max(0.0, min(1.0, _as_float(getattr(policy_decision, "confidence", 0.0), default=0.0))),
            "rationale": str(getattr(policy_decision, "rationale", "") or ""),
        }

    def _resolve_memory_ref(self) -> Any | None:
        for agent in self.agent_roster:
            candidate = getattr(agent, "memory_ref", None)
            if candidate is not None and callable(getattr(candidate, "save", None)):
                return candidate
        return None

    def _log_event(self, *, event_name: str, payload: dict[str, Any]) -> None:
        if self._memory_ref is None:
            return
        content = {
            "council_event": str(event_name),
            "project_id": self.project_id,
            "domain": self.domain,
            "council_id": self.council_id,
            "payload": dict(payload),
            "created_at": _utc_now_iso(),
        }
        self._memory_ref.save(self.project_id, self.domain, "event", content)


def _collect_unique(deliberation_result: dict[str, Any], *, path: tuple[str, str, str]) -> list[str]:
    rounds = list(deliberation_result.get(path[0]) or [])
    out: list[str] = []
    for item in rounds:
        if not isinstance(item, dict):
            continue
        mid = item.get(path[1])
        if not isinstance(mid, dict):
            continue
        values = mid.get(path[2])
        if not isinstance(values, list):
            continue
        out.extend(str(value).strip() for value in values if str(value).strip())
    return sorted(set(out))


def _to_agent_vote(vote: dict[str, Any], *, index: int) -> AgentVote:
    recommendation = str(vote.get("recommendation") or "").strip().lower()
    confidence = max(0.0, min(1.0, _as_float(vote.get("confidence"), default=0.0)))
    score_a = confidence if recommendation == "approve" else (1.0 - confidence)
    role = str(vote.get("role") or "agent").strip().lower()
    agent_type = AgentType.MAMBA if role in {"domain_expert", "historian_agent"} else AgentType.LLM
    return AgentVote(
        agent_id=str(vote.get("role") or f"agent-{index+1}"),
        agent_type=agent_type,
        round=1,
        score_a=max(0.0, min(1.0, score_a)),
        confidence=confidence,
        rationale=str(vote.get("rationale") or ""),
        reputation_weight=max(0.0, _as_float(vote.get("weight"), default=1.0)),
        raw_output="",
    )


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _profile_to_config(profile: Any) -> dict[str, Any]:
    as_config = getattr(profile, "as_config", None)
    if callable(as_config):
        payload = as_config()
        if isinstance(payload, dict):
            return dict(payload)
    if isinstance(profile, dict):
        return dict(profile)
    return {}


def _profile_roster(profile: Any) -> list[str]:
    if isinstance(profile, dict):
        roster = profile.get("agent_roster", [])
    else:
        roster = getattr(profile, "agent_roster", [])
    if not isinstance(roster, list):
        return []
    return [str(role).strip() for role in roster if str(role).strip()]


def _profile_domain(profile: Any, *, default: str) -> str:
    if isinstance(profile, dict):
        domains = profile.get("domains", [])
    else:
        domains = getattr(profile, "domains", [])
    if isinstance(domains, list) and domains:
        candidate = str(domains[0]).strip()
        if candidate:
            return candidate
    return str(default or "general")


def _spawn_consensus_engine(engine: Any) -> Any:
    cls = engine.__class__
    try:
        return cls()
    except Exception:
        return engine


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
