from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from enum import Enum
import json
import logging
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field

try:
    from redis import exceptions as _redis_exc
    _REDIS_CONN_ERRORS = (_redis_exc.ConnectionError, _redis_exc.TimeoutError)
except Exception:
    _REDIS_CONN_ERRORS = ()  # type: ignore[assignment]

_log = logging.getLogger(__name__)


class SkillSource(Enum):
    LOCAL = "local"
    CODEX = "codex"
    GITHUB = "github"
    HUGGINGFACE = "huggingface"


class SkillRecord(BaseModel):
    skill_id: str
    source: SkillSource
    uri: str
    domains: List[str]
    dimensions: List[str] = Field(default_factory=list)
    content: str
    fetched_at: datetime
    ttl_seconds: int = 3600
    token_count: int = 0
    sandboxed: bool = False
    sandbox_until: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        if not self.sandboxed:
            return True
        if self.sandbox_until is None:
            return False
        until = self.sandbox_until
        if until.tzinfo is None:
            return datetime.utcnow() > until
        return datetime.now(tz=until.tzinfo) > until


ROLE_DIMENSION_MAP = {
    "Supervisor": ["coordination", "risk", "general"],
    "Architect": ["architecture", "design", "patterns", "general"],
    "Validation": ["testing", "quality", "invariants", "general"],
    "Repair": ["debugging", "antipatterns", "general"],
    "Translator": ["output", "formatting", "conventions", "general"],
}


class SkillBundle(BaseModel):
    skills: List[SkillRecord] = Field(default_factory=list)
    domain: str = ""
    token_used: int = 0

    @property
    def is_empty(self) -> bool:
        return len(self.skills) == 0

    @property
    def skill_ids(self) -> List[str]:
        return [s.skill_id for s in self.skills]

    @classmethod
    def empty(cls) -> "SkillBundle":
        return cls()

    def for_role(self, role: str) -> Optional["SkillBundle"]:
        relevant_dims = ROLE_DIMENSION_MAP.get(role, ["general"])
        filtered = [
            s
            for s in self.skills
            if not s.dimensions or any(d in relevant_dims for d in s.dimensions)
        ]
        if not filtered:
            return None
        return SkillBundle(
            skills=filtered,
            domain=self.domain,
            token_used=sum(s.token_count for s in filtered),
        )

    def as_context(self) -> str:
        parts = []
        for skill in self.skills:
            parts.append(
                f"## Domain Knowledge: {skill.skill_id} (source: {skill.source.value})\n"
                f"{skill.content}"
            )
        return "\n\n".join(parts)

    def provide(self, context: Dict[str, Any]) -> Dict[str, Any]:
        role = str(context.get("role") or "").strip()
        filtered: SkillBundle = self.for_role(role) if role else self
        if filtered is None:
            filtered = SkillBundle.empty()
        return {
            "skill_context": filtered.as_context(),
            "skill_ids": [r.skill_id for r in filtered.skills],
            "token_count": sum(r.token_count for r in filtered.skills),
        }


class SkillRegistry:
    """
    Primary storage: Redis (key: 'skill:{skill_id}', TTL from record).
    Secondary storage: optional state manager, best-effort.
    Index: domain -> set[skill_id] in Redis.
    """

    DOMAIN_INDEX_PREFIX = "skill_idx:domain:"

    def __init__(self, redis_client: Any, state_manager: Any) -> None:
        self.redis = redis_client
        self.db = state_manager
        self._memory: Dict[str, SkillRecord] = {}
        self._domain_index: Dict[str, Set[str]] = defaultdict(set)

    async def register(self, record: SkillRecord) -> None:
        key = f"skill:{record.skill_id}"
        payload = _record_to_json(record)
        try:
            await self.redis.setex(key, int(record.ttl_seconds), payload)
            for domain in record.domains:
                await self.redis.sadd(f"{self.DOMAIN_INDEX_PREFIX}{domain}", record.skill_id)
        except _REDIS_CONN_ERRORS:
            _log.warning("Redis unavailable; using in-memory fallback for skill %s", record.skill_id)
            self._memory[record.skill_id] = record
            for domain in record.domains:
                self._domain_index[domain].add(record.skill_id)
        await self._persist_secondary(record)

    async def lookup(self, domain: str, dimension: str = "") -> List[SkillRecord]:
        try:
            raw_ids = await self.redis.smembers(f"{self.DOMAIN_INDEX_PREFIX}{domain}")
        except _REDIS_CONN_ERRORS:
            _log.warning("Redis unavailable; using in-memory fallback for domain %s", domain)
            return self._lookup_memory(domain, dimension)
        wanted_dimension = str(dimension or "").strip().lower()
        records: List[SkillRecord] = []
        for sid in raw_ids:
            normalized_id = _as_text(sid)
            if not normalized_id:
                continue
            raw = await self.redis.get(f"skill:{normalized_id}")
            if not raw:
                continue
            try:
                record = _record_from_json(raw)
            except Exception:
                continue
            if not record.is_active:
                continue
            if wanted_dimension and record.dimensions:
                dims = {str(dim).strip().lower() for dim in record.dimensions if str(dim).strip()}
                if wanted_dimension not in dims:
                    continue
            records.append(record)
        return sorted(records, key=lambda item: item.fetched_at, reverse=True)

    def _lookup_memory(self, domain: str, dimension: str) -> List[SkillRecord]:
        wanted_dim = str(dimension or "").strip().lower()
        ids = self._domain_index.get(domain, set())
        records: List[SkillRecord] = []
        for sid in ids:
            record = self._memory.get(sid)
            if record is None or not record.is_active:
                continue
            if wanted_dim and record.dimensions:
                dims = {str(d).strip().lower() for d in record.dimensions if str(d).strip()}
                if wanted_dim not in dims:
                    continue
            records.append(record)
        return sorted(records, key=lambda r: r.fetched_at, reverse=True)

    async def set_sandboxed(self, skill_id: str, hours: int) -> None:
        raw = await self.redis.get(f"skill:{skill_id}")
        if not raw:
            return
        try:
            record = _record_from_json(raw)
        except Exception:
            return
        record.sandboxed = True
        record.sandbox_until = datetime.utcnow() + timedelta(hours=int(hours))
        await self.redis.setex(
            f"skill:{skill_id}",
            int(record.ttl_seconds),
            _record_to_json(record),
        )
        await self._persist_secondary(record)

    async def _persist_secondary(self, record: SkillRecord) -> None:
        if self.db is None:
            return
        methods = ("upsert_skill", "save_skill", "register_skill")
        for method_name in methods:
            method = getattr(self.db, method_name, None)
            if callable(method):
                try:
                    result = method(record)
                    if hasattr(result, "__await__"):
                        await result
                except Exception:
                    return
                return


def _record_to_json(record: SkillRecord) -> str:
    if hasattr(record, "model_dump_json"):
        return record.model_dump_json()
    return record.json()


def _record_from_json(raw: Any) -> SkillRecord:
    text = _as_text(raw)
    payload = json.loads(text)
    if hasattr(SkillRecord, "model_validate"):
        return SkillRecord.model_validate(payload)
    return SkillRecord.parse_obj(payload)


def _as_text(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw or "")

