from __future__ import annotations

from statistics import median, pstdev
from typing import List

from babyai.policy_consensus.models import AgentType, AgentVote, Conflict, PolicyDecision


class VoteAggregator:
    FALLBACK_CONFIDENCE = 0.40

    def resolve(self, votes: List[AgentVote], conflict: Conflict) -> PolicyDecision:
        filtered = self.filter_outliers(votes)
        active = filtered if filtered else list(votes)
        if not active:
            winner = self._priority_winner(conflict=conflict)
            return PolicyDecision(
                winning_policy=winner,
                confidence=0.0,
                rounds_to_converge=1,
                dissent_ratio=0.0,
                rationale="priority-fallback",
                fallback_used=True,
            )

        weighted_a = self._weighted_a(active)
        winner = conflict.policy_a if weighted_a > 0.5 else conflict.policy_b
        confidence = abs(weighted_a - 0.5) * 2.0
        dissent = self._dissent_ratio(active, winner_is_a=(winner.policy_id == conflict.policy_a.policy_id))

        if confidence < self.FALLBACK_CONFIDENCE:
            priority_winner = self._priority_winner(conflict=conflict)
            return PolicyDecision(
                winning_policy=priority_winner,
                confidence=confidence,
                rounds_to_converge=1,
                dissent_ratio=dissent,
                rationale="priority-fallback",
                fallback_used=True,
            )

        return PolicyDecision(
            winning_policy=winner,
            confidence=confidence,
            rounds_to_converge=1,
            dissent_ratio=dissent,
            rationale=self._rationale(active, conflict=conflict),
            fallback_used=False,
        )

    def filter_outliers(self, votes: List[AgentVote]) -> List[AgentVote]:
        if len(votes) < 3:
            return list(votes)
        scores = [float(v.score_a) for v in votes]
        center = float(median(scores))
        spread = float(pstdev(scores))
        if spread <= 0.0:
            return list(votes)
        threshold = 2.0 * spread
        filtered = [v for v in votes if abs(float(v.score_a) - center) <= threshold]
        return filtered if filtered else list(votes)

    def _weighted_a(self, votes: List[AgentVote]) -> float:
        total_weight = 0.0
        weighted_sum = 0.0
        for vote in votes:
            w = float(vote.reputation_weight)
            if w <= 0:
                continue
            total_weight += w
            weighted_sum += float(vote.score_a) * w
        if total_weight <= 0.0:
            return 0.5
        return weighted_sum / total_weight

    def _priority_winner(self, *, conflict: Conflict):
        if int(conflict.policy_a.priority) >= int(conflict.policy_b.priority):
            return conflict.policy_a
        return conflict.policy_b

    def _dissent_ratio(self, votes: List[AgentVote], *, winner_is_a: bool) -> float:
        if not votes:
            return 0.0
        dissent = 0
        for vote in votes:
            vote_for_a = float(vote.score_a) > 0.5
            if vote_for_a != bool(winner_is_a):
                dissent += 1
        return float(dissent) / float(len(votes))

    def _rationale(self, votes: List[AgentVote], *, conflict: Conflict) -> str:
        llm_rationales: List[str] = []
        for vote in votes:
            if vote.agent_type != AgentType.LLM:
                continue
            text = str(vote.rationale or "").strip()
            if text:
                llm_rationales.append(text)
            if len(llm_rationales) >= 3:
                break
        if llm_rationales:
            return "; ".join(llm_rationales)
        return f"{conflict.policy_a.domain}-policy valgt via konsensus"

