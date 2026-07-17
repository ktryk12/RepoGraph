from __future__ import annotations

from itertools import combinations
import os
import re
from typing import Any, List

from babyai.policy_consensus.models import Conflict, PolicyDirective

_DEFAULT_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


class ConflictDetector:
    def __init__(self, *, llm_client: Any, model_name: str = _DEFAULT_MODEL) -> None:
        self.llm_client = llm_client
        self.model_name = str(model_name)

    async def scan(self, policies: List[PolicyDirective], request_context: str) -> List[Conflict]:
        conflicts: List[Conflict] = []
        for policy_a, policy_b in combinations(list(policies), 2):
            severity = await self._score(policy_a, policy_b)
            if severity <= 0.30:
                continue
            conflicts.append(
                Conflict(
                    policy_a=policy_a,
                    policy_b=policy_b,
                    dimension=self._dimension(policy_a, policy_b),
                    severity=severity,
                    request_context=str(request_context),
                )
            )
        return sorted(conflicts, key=lambda row: float(row.severity), reverse=True)

    async def _score(self, policy_a: PolicyDirective, policy_b: PolicyDirective) -> float:
        prompt = (
            "Rate conflict 0.0-1.0 between these two policy directives.\n"
            f"Policy A: {policy_a.directive}\n"
            f"Policy B: {policy_b.directive}\n"
            f"Priority A: {policy_a.priority}\n"
            f"Priority B: {policy_b.priority}\n"
            "Return only a number."
        )
        try:
            raw = await self.llm_client.complete(
                system="You output a single numeric conflict severity between 0 and 1.",
                user=prompt,
                max_tokens=20,
                model=self.model_name,
            )
        except TypeError:
            try:
                raw = await self.llm_client.complete(
                    system="You output a single numeric conflict severity between 0 and 1.",
                    user=prompt,
                    max_tokens=20,
                )
            except Exception:
                return 0.0
        except Exception:
            return 0.0
        return _parse_score(raw)

    def _dimension(self, policy_a: PolicyDirective, policy_b: PolicyDirective) -> str:
        if int(policy_a.priority) != int(policy_b.priority):
            return "priority"

        tokens_a = _tokenize(policy_a.directive)
        tokens_b = _tokenize(policy_b.directive)
        restrict_terms = {"must", "never", "forbid", "forbidden", "deny", "only", "cannot", "disallow"}
        a_restrict = bool(tokens_a & restrict_terms)
        b_restrict = bool(tokens_b & restrict_terms)
        if a_restrict != b_restrict:
            return "restriction"

        tag_a = {str(tag).strip().lower() for tag in policy_a.tags if str(tag).strip()}
        tag_b = {str(tag).strip().lower() for tag in policy_b.tags if str(tag).strip()}
        if tag_a != tag_b:
            return "scope"
        return "tone"


def _parse_score(raw: Any) -> float:
    text = str(raw or "").strip()
    if not text:
        return 0.0
    match = re.search(r"[-+]?\d*\.?\d+", text)
    if not match:
        return 0.0
    try:
        score = float(match.group(0))
    except Exception:
        return 0.0
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return score


def _tokenize(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-zA-Z]+", str(text or "").lower()) if tok}

