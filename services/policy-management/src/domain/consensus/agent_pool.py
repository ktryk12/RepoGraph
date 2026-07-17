from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, List, Sequence

from pydantic import BaseModel, Field

from babyai.policy_consensus.base_agent import BaseAgent
from babyai.policy_consensus.llm_agent import LLMAgent
from babyai.policy_consensus.mamba_agent import MambaAgent
from babyai.policy_consensus.models import AgentType, AgentVote, Conflict, PolicyDirective
from babyai.policy_consensus.reputation_tracker import ReputationTracker
from babyai.policy_consensus.vote_aggregator import VoteAggregator


class VerifyResult(BaseModel):
    agrees_with_proposal: bool
    confidence: float = Field(ge=0.0, le=1.0)
    counter_decision: PolicyDirective | None = None
    vote_breakdown: List[AgentVote] = Field(default_factory=list)


class _NoopLLMClient:
    async def complete(self, *, system: str, user: str, max_tokens: int, model: str) -> str:
        del system, user, max_tokens, model
        return '{"score_a": 0.5, "confidence": 0.5, "rationale": "noop"}'


class AgentPoolOrchestrator:
    CONVERGENCE_THRESHOLD = 0.05

    def __init__(
        self,
        *,
        reputation_tracker: ReputationTracker,
        vote_aggregator: VoteAggregator | None = None,
        mamba_agent_factory: Callable[[str, float], BaseAgent] | None = None,
        llm_agent_factory: Callable[[str, float], BaseAgent] | None = None,
    ) -> None:
        self.reputation_tracker = reputation_tracker
        self.vote_aggregator = vote_aggregator or VoteAggregator()
        self._mamba_agent_factory = mamba_agent_factory or self._default_mamba_factory
        self._llm_agent_factory = llm_agent_factory or self._default_llm_factory

    async def _build_agents(self, severity: float) -> List[BaseAgent]:
        mamba_count, llm_count = _pool_counts(float(severity))
        agents: List[BaseAgent] = []

        for index in range(mamba_count):
            agent_id = f"mamba-{index + 1:02d}"
            reputation = await self.reputation_tracker.get(agent_id)
            agents.append(self._mamba_agent_factory(agent_id, float(reputation)))

        for index in range(llm_count):
            agent_id = f"llm-{index + 1:02d}"
            reputation = await self.reputation_tracker.get(agent_id)
            agents.append(self._llm_agent_factory(agent_id, float(reputation)))

        return agents

    async def convene(self, conflict: Conflict) -> List[AgentVote]:
        agents = await self._build_agents(conflict.severity)
        if not agents:
            return []

        all_votes: List[AgentVote] = []
        round_votes = await self._run_evaluate_round(
            agents=agents,
            conflict=conflict,
            peer_votes=[],
            round_num=1,
        )
        all_votes.extend(round_votes)
        await self._update_reputation(round_votes)
        previous_avg_conf = _avg_confidence(round_votes)

        for round_num in (2, 3):
            if previous_avg_conf >= 0.65:
                active_agents = [agent for agent in agents if agent.agent_type == AgentType.MAMBA]  # type: ignore[attr-defined]
            else:
                active_agents = list(agents)
            if not active_agents:
                break

            await self._refresh_reputation_weights(active_agents)
            round_votes = await self._run_evaluate_round(
                agents=active_agents,
                conflict=conflict,
                peer_votes=list(all_votes),
                round_num=round_num,
            )
            if not round_votes:
                break
            all_votes.extend(round_votes)
            await self._update_reputation(round_votes)

            current_avg_conf = _avg_confidence(round_votes)
            if current_avg_conf >= 0.75:
                break
            if abs(current_avg_conf - previous_avg_conf) <= self.CONVERGENCE_THRESHOLD:
                break
            previous_avg_conf = current_avg_conf

        return all_votes

    async def verify(self, conflict: Conflict, proposed_winner: PolicyDirective) -> VerifyResult:
        agents = await self._build_agents(conflict.severity)
        await self._refresh_reputation_weights(agents)
        votes = await asyncio.gather(
            *[
                agent.verify(conflict=conflict, proposed_winner=proposed_winner, round_num=99)
                for agent in agents
            ]
        )
        if not votes:
            return VerifyResult(
                agrees_with_proposal=False,
                confidence=0.0,
                vote_breakdown=[],
            )

        agree_count = sum(1 for vote in votes if float(vote.score_a) > 0.5)
        agree_ratio = float(agree_count) / float(len(votes))
        counter: PolicyDirective | None = None
        if agree_ratio <= 0.5:
            if str(proposed_winner.policy_id) == str(conflict.policy_a.policy_id):
                counter = conflict.policy_b
            else:
                counter = conflict.policy_a
        return VerifyResult(
            agrees_with_proposal=agree_ratio > 0.5,
            confidence=abs(agree_ratio - 0.5) * 2.0,
            counter_decision=counter,
            vote_breakdown=list(votes),
        )

    async def _run_evaluate_round(
        self,
        *,
        agents: Sequence[BaseAgent],
        conflict: Conflict,
        peer_votes: List[AgentVote],
        round_num: int,
    ) -> List[AgentVote]:
        results = await asyncio.gather(
            *[
                agent.evaluate(conflict=conflict, peer_votes=list(peer_votes), round_num=int(round_num))
                for agent in agents
            ]
        )
        return list(results)

    async def _update_reputation(self, votes: Sequence[AgentVote]) -> None:
        if not votes:
            return
        avg_score = sum(float(v.score_a) for v in votes) / float(len(votes))
        majority_for_a = avg_score > 0.5
        await asyncio.gather(
            *[
                self.reputation_tracker.update(
                    vote.agent_id,
                    was_majority=((float(vote.score_a) > 0.5) == majority_for_a),
                )
                for vote in votes
            ]
        )

    async def _refresh_reputation_weights(self, agents: Sequence[BaseAgent]) -> None:
        for agent in agents:
            value = await self.reputation_tracker.get(agent.agent_id)
            agent.reputation_weight = float(value)  # type: ignore[attr-defined]

    def _default_mamba_factory(self, agent_id: str, reputation_weight: float) -> BaseAgent:
        return MambaAgent(
            agent_id=str(agent_id),
            model_manager_url="http://model-runner:8081",
            reputation_weight=float(reputation_weight),
        )

    def _default_llm_factory(self, agent_id: str, reputation_weight: float) -> BaseAgent:
        return LLMAgent(
            agent_id=str(agent_id),
            llm_client=_NoopLLMClient(),
            reputation_weight=float(reputation_weight),
        )


def _pool_counts(severity: float) -> tuple[int, int]:
    if severity < 0.4:
        return 5, 1
    if severity < 0.7:
        return 8, 2
    return 10, 5


def _avg_confidence(votes: Sequence[AgentVote]) -> float:
    if not votes:
        return 0.0
    return sum(float(v.confidence) for v in votes) / float(len(votes))
