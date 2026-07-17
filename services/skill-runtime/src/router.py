from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from typing import Any, List

from babyai.skills.registry import SkillBundle, SkillRecord

MAX_SKILLS = int(os.getenv("SKILL_MAX_SKILLS", "3"))
MAX_TOKENS = int(os.getenv("SKILL_MAX_TOKENS", "2000"))
CACHE_TTL  = int(os.getenv("SKILL_CACHE_TTL",  "3600"))


class SkillRouter:
    MAX_TOKENS_PER_ROLE = MAX_TOKENS
    MAX_SKILLS = MAX_SKILLS
    CACHE_PREFIX = "skills:bundle:"
    CACHE_TTL_SECONDS = CACHE_TTL

    def __init__(self, *, registry: Any, redis_client: Any) -> None:
        self.registry = registry
        self.redis = redis_client

    async def resolve(self, domain: str, dimension: str = "", role: str = "") -> SkillBundle:
        cache_key = f"{self.CACHE_PREFIX}{domain}:{dimension}:{role}"
        cached = await self.redis.get(cache_key)
        if cached:
            bundle = _bundle_from_json(cached)
            if bundle is not None:
                return bundle

        records = await self.registry.lookup(domain, dimension)
        if not records:
            records = await self.registry.lookup("general")

        ranked = self._rank(records, requested_domain=domain)
        trimmed = self._trim(ranked)
        bundle = SkillBundle(
            skills=trimmed,
            domain=str(domain),
            token_used=sum(int(max(0, s.token_count)) for s in trimmed),
        )
        if role:
            filtered = bundle.for_role(role)
            if filtered is not None:
                bundle = filtered

        await self.redis.setex(cache_key, self.CACHE_TTL_SECONDS, _bundle_to_json(bundle))
        return bundle

    def _rank(self, records: List[SkillRecord], *, requested_domain: str) -> List[SkillRecord]:
        now = datetime.now(timezone.utc)

        def _score(record: SkillRecord) -> float:
            domain_exact = 1.0 if _has_domain(record, requested_domain) else 0.0
            recency = _recency_score(record=record, now=now)
            return (0.7 * domain_exact) + (0.3 * recency)

        return sorted(
            list(records),
            key=lambda rec: (_score(rec), rec.fetched_at),
            reverse=True,
        )

    def _trim(self, records: List[SkillRecord]) -> List[SkillRecord]:
        selected: List[SkillRecord] = []
        used_tokens = 0
        for record in records:
            if len(selected) >= self.MAX_SKILLS:
                break
            record_tokens = int(max(0, record.token_count))
            next_total = used_tokens + record_tokens
            if next_total > self.MAX_TOKENS_PER_ROLE:
                break
            selected.append(record)
            used_tokens = next_total
        return selected


def _bundle_to_json(bundle: SkillBundle) -> str:
    if hasattr(bundle, "model_dump_json"):
        return bundle.model_dump_json()
    return bundle.json()


def _bundle_from_json(raw: Any) -> SkillBundle | None:
    text = _as_text(raw)
    if not text.strip():
        return None
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if hasattr(SkillBundle, "model_validate"):
        return SkillBundle.model_validate(payload)
    return SkillBundle.parse_obj(payload)


def _as_text(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")


def _has_domain(record: SkillRecord, requested_domain: str) -> bool:
    expected = str(requested_domain or "").strip().lower()
    if not expected:
        return False
    domains = {str(value).strip().lower() for value in record.domains if str(value).strip()}
    return expected in domains


def _recency_score(*, record: SkillRecord, now: datetime) -> float:
    fetched = record.fetched_at
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    delta_seconds = max(0.0, (now - fetched).total_seconds())
    days_since_fetch = delta_seconds / 86400.0
    return 1.0 / (1.0 + days_since_fetch)

