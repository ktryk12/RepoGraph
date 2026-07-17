from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from babyai.policy_consensus.models import AgentType, AgentVote, Conflict, PolicyDirective
from babyai.skills.registry import SkillBundle


class BaseAgent(ABC):
    def __init__(
        self,
        agent_id: str,
        agent_type: AgentType,
        reputation_weight: float = 1.0,
        skill_bundle: Optional[SkillBundle] = None,
    ) -> None:
        self.agent_id = str(agent_id)
        self.agent_type = agent_type
        self.reputation_weight = float(reputation_weight)
        self.skill_bundle = skill_bundle

    @abstractmethod
    async def evaluate(
        self,
        conflict: Conflict,
        peer_votes: List[AgentVote],
        round_num: int,
    ) -> AgentVote:
        ...

    @abstractmethod
    async def verify(
        self,
        conflict: Conflict,
        proposed_winner: PolicyDirective,
        round_num: int,
    ) -> AgentVote:
        # KRITISK: verify() prompt MÅ IKKE indeholde peer_votes.
        ...

    def _skill_context(self) -> str:
        if self.skill_bundle:
            return self.skill_bundle.as_context()
        return ""

