from __future__ import annotations

import asyncio
import json
import re
import statistics
from typing import List

import aiohttp

from babyai.policy_consensus.base_agent import BaseAgent
from babyai.policy_consensus.models import AgentType, AgentVote, Conflict, PolicyDirective
from babyai.skills.registry import SkillBundle


class MambaAgent(BaseAgent):
    TIMEOUT_MS = 0.050

    def __init__(
        self,
        *,
        agent_id: str,
        model_manager_url: str,
        reputation_weight: float = 1.0,
        skill_bundle: SkillBundle | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            agent_type=AgentType.MAMBA,
            reputation_weight=reputation_weight,
            skill_bundle=skill_bundle,
        )
        self.model_manager_url = str(model_manager_url).rstrip("/")

    async def evaluate(
        self,
        conflict: Conflict,
        peer_votes: List[AgentVote],
        round_num: int,
    ) -> AgentVote:
        prompt = self._build_evaluate_prompt(conflict=conflict, round_num=round_num)
        fallback_used = False
        raw_output = ""
        score_a = 0.5
        rationale = "model_response"
        try:
            raw_output = await asyncio.wait_for(self._call_model(prompt), timeout=self.TIMEOUT_MS)
            score_a = _clamp_01(_parse_score(raw_output, default=0.5))
        except asyncio.TimeoutError:
            fallback_used = True
            score_a = _clamp_01(self._peer_median(peer_votes))
            rationale = "timeout_fallback_peer_median"

        if fallback_used:
            raw_output = raw_output or "timeout"

        return AgentVote(
            agent_id=self.agent_id,
            agent_type=AgentType.MAMBA,
            round=int(round_num),
            score_a=score_a,
            confidence=0.7,
            rationale=rationale,
            reputation_weight=self.reputation_weight,
            raw_output=raw_output,
        )

    async def verify(
        self,
        conflict: Conflict,
        proposed_winner: PolicyDirective,
        round_num: int,
    ) -> AgentVote:
        prompt = self._build_verify_prompt(
            conflict=conflict,
            proposed_winner=proposed_winner,
            round_num=round_num,
        )
        raw_output = ""
        score_a = 0.5
        rationale = "verify_response"
        try:
            raw_output = await asyncio.wait_for(self._call_model(prompt), timeout=self.TIMEOUT_MS)
            score_a = _clamp_01(_parse_score(raw_output, default=0.5))
        except asyncio.TimeoutError:
            raw_output = "timeout"
            score_a = 0.5
            rationale = "verify_timeout"

        return AgentVote(
            agent_id=self.agent_id,
            agent_type=AgentType.MAMBA,
            round=int(round_num),
            score_a=score_a,
            confidence=0.7,
            rationale=rationale,
            reputation_weight=self.reputation_weight,
            raw_output=raw_output,
        )

    def can_explain(self) -> bool:
        return False

    def _peer_median(self, votes: List[AgentVote]) -> float:
        if not votes:
            return 0.5
        return float(statistics.median(v.score_a for v in votes))

    async def _call_model(self, prompt: str) -> str:
        url = f"{self.model_manager_url}/v1/completions"
        payload = {
            "prompt": str(prompt),
            "max_tokens": 64,
            "temperature": 0.0,
        }
        timeout = aiohttp.ClientTimeout(total=210.0)  # Must cover cold start; measured mamba ~50s (2026-03-20)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as response:
                    if int(response.status) >= 400:
                        return ""
                    text = await response.text()
        except Exception:
            return ""

        parsed = _extract_text_from_completion_response(text)
        return parsed or text

    def _build_evaluate_prompt(self, *, conflict: Conflict, round_num: int) -> str:
        skill_context = self._skill_context()
        parts = []
        if skill_context:
            parts.append(skill_context)
        parts.append(
            "You are scoring policy conflict options.\n"
            f"Round: {int(round_num)}\n"
            f"Dimension: {conflict.dimension}\n"
            f"Policy A: {conflict.policy_a.directive}\n"
            f"Policy B: {conflict.policy_b.directive}\n"
            f"Request Context: {conflict.request_context}\n"
            "Score 0.0=A 1.0=B. Return only the numeric score."
        )
        return "\n\n".join(parts).strip()

    def _build_verify_prompt(
        self,
        *,
        conflict: Conflict,
        proposed_winner: PolicyDirective,
        round_num: int,
    ) -> str:
        skill_context = self._skill_context()
        parts = []
        if skill_context:
            parts.append(skill_context)
        parts.append(
            "Verify the proposed policy winner.\n"
            f"Round: {int(round_num)}\n"
            f"Proposed resolution: {proposed_winner.directive}\n"
            f"Context: {conflict.request_context}\n"
            "Is this correct? Score 0=no 1=yes:"
        )
        return "\n\n".join(parts).strip()


def _parse_score(raw: str, *, default: float) -> float:
    text = str(raw or "").strip()
    if not text:
        return float(default)
    try:
        value = float(text)
        return value
    except Exception:
        pass

    json_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if json_match:
        try:
            payload = json.loads(json_match.group(0))
            if isinstance(payload, dict):
                for key in ("score", "score_a", "value", "confidence"):
                    try:
                        return float(payload[key])  # type: ignore[index]
                    except Exception:
                        continue
        except Exception:
            pass

    number_match = re.search(r"[-+]?\d*\.?\d+", text)
    if number_match:
        try:
            return float(number_match.group(0))
        except Exception:
            return float(default)
    return float(default)


def _extract_text_from_completion_response(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except Exception:
        return text
    if isinstance(payload, dict):
        for key in ("text", "completion", "output", "response"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
    return text


def _clamp_01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)

