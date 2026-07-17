from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any, Callable, List

from babyai.policy_consensus.agent_pool import AgentPoolOrchestrator
from babyai.policy_consensus.models import Conflict, FinalDecision, PolicyDecision, PolicyDirective
from babyai.policy_consensus.vote_aggregator import VoteAggregator


@dataclass(frozen=True)
class _RoundState:
    decision: PolicyDecision


class RecursiveVerificationEngine:
    STOP_THRESHOLD = 0.90
    MAX_VERIFY_ROUNDS = 4
    FALLBACK_THRESHOLD = 0.45

    def __init__(
        self,
        *,
        skill_router: Any,
        pool_factory: Callable[..., AgentPoolOrchestrator],
        vote_aggregator: VoteAggregator | None = None,
    ) -> None:
        self.skill_router = skill_router
        self.pool_factory = pool_factory
        self.vote_aggregator = vote_aggregator or VoteAggregator()

    async def resolve(self, conflict: Conflict) -> FinalDecision:
        t0 = time.time()
        domain = str(conflict.policy_a.domain or conflict.policy_b.domain or "general")
        mamba_skills = await self.skill_router.resolve(domain)
        llm_skills = await self.skill_router.resolve(domain)

        pool = self.pool_factory(
            severity=float(conflict.severity),
            mamba_skills=mamba_skills,
            llm_skills=llm_skills,
        )
        votes = await pool.convene(conflict)
        decision = self.vote_aggregator.resolve(votes, conflict)
        history: List[_RoundState] = [_RoundState(decision=decision)]
        acc_conf = float(decision.confidence)

        for round_num in range(2, self.MAX_VERIFY_ROUNDS + 1):
            if acc_conf >= self.STOP_THRESHOLD:
                break
            if acc_conf < self.FALLBACK_THRESHOLD:
                fallback = self._priority_fallback(conflict)
                return FinalDecision(
                    winning_policy=fallback,
                    final_confidence=acc_conf,
                    rounds_used=len(history),
                    fallback_used=True,
                    total_duration_ms=(time.time() - t0) * 1000.0,
                    skills_used=_skill_ids(llm_skills),
                )

            verify = await pool.verify(conflict, decision.winning_policy)
            if (not verify.agrees_with_proposal) and verify.counter_decision is not None:
                winning = verify.counter_decision
                rationale = "verification-counter-decision"
            else:
                winning = decision.winning_policy
                rationale = "verification-confirmed"

            decision = PolicyDecision(
                winning_policy=winning,
                confidence=float(verify.confidence),
                rounds_to_converge=int(round_num),
                dissent_ratio=_verify_dissent_ratio(verify.vote_breakdown, verify.agrees_with_proposal),
                rationale=rationale,
                fallback_used=False,
            )
            history.append(_RoundState(decision=decision))
            acc_conf = _accumulate_confidence(history)

        return FinalDecision(
            winning_policy=decision.winning_policy,
            final_confidence=acc_conf,
            rounds_used=len(history),
            fallback_used=False,
            total_duration_ms=(time.time() - t0) * 1000.0,
            skills_used=_skill_ids(llm_skills),
        )

    def _priority_fallback(self, conflict: Conflict) -> PolicyDirective:
        if int(conflict.policy_a.priority) >= int(conflict.policy_b.priority):
            return conflict.policy_a
        return conflict.policy_b


def _accumulate_confidence(history: List[_RoundState]) -> float:
    if not history:
        return 0.0
    weighted_sum = 0.0
    total_weight = 0.0
    total = len(history)
    for idx, row in enumerate(history):
        age = (total - 1) - idx
        weight = 0.5 ** age
        total_weight += weight
        weighted_sum += float(row.decision.confidence) * weight
    if total_weight <= 0.0:
        return 0.0
    value = weighted_sum / total_weight
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def _verify_dissent_ratio(votes: List[Any], agrees_with_proposal: bool) -> float:
    if not votes:
        return 0.0
    agree = sum(1 for vote in votes if float(getattr(vote, "score_a", 0.0)) > 0.5)
    agree_ratio = float(agree) / float(len(votes))
    return (1.0 - agree_ratio) if agrees_with_proposal else agree_ratio


def _skill_ids(bundle: Any) -> List[str]:
    values = getattr(bundle, "skill_ids", None)
    if isinstance(values, list):
        return [str(item) for item in values if str(item).strip()]
    if isinstance(bundle, dict):
        out: List[str] = []
        for row in list(bundle.get("skills", []) or []):
            if not isinstance(row, dict):
                continue
            skill_id = str(row.get("skill_id") or "").strip()
            if skill_id:
                out.append(skill_id)
        return out
    return []

