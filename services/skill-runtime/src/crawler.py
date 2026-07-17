from __future__ import annotations

import asyncio
import importlib
from datetime import datetime
from hashlib import sha256
import json
import logging
import os
import re
from typing import Any, Dict, List, Mapping, Optional

_log = logging.getLogger(__name__)

import aiohttp
from pydantic import BaseModel, Field

from babyai.skills.fetchers.github_fetcher import GitHubSkillFetcher
from babyai.skills.registry import SkillRecord, SkillSource


class SkillManifest(BaseModel):
    """Represents a fetched skill from a specific repo."""
    skill_id: str
    repo_url: str
    skill_name: str
    content: str
    install_requires: List[str] = Field(default_factory=list)
    entry_point: str = ""

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
_MAX_RESULTS = 10
SKILL_FILE_NAME = "SKILL.md"


class SkillUpdateEvent(BaseModel):
    domain: str
    dimension: str = ""
    query: str
    timestamp: datetime


class SkillCrawler:
    CHANNEL = "babyai:skill_updates"
    _TOP_CANDIDATES = 5
    _VALIDATION_CONTENT_CHARS = 2000
    _MIN_ACCEPT_SCORE = 0.70

    def __init__(
        self,
        *,
        redis_client: Any = None,
        registry: Any = None,
        expert_api: Any = None,
        github_fetcher: GitHubSkillFetcher | None = None,
        github_token: str = "",
        expert_api_url: str = "",
    ) -> None:
        self.redis = redis_client
        self.registry = registry
        self.expert_api = expert_api
        self.github_fetcher = github_fetcher or GitHubSkillFetcher()
        self.github_token = str(github_token or "").strip()
        self.expert_api_url = str(expert_api_url or "").rstrip("/")
        self._running = False

    async def listen(self) -> None:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.CHANNEL)
        self._running = True
        while self._running:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if not message:
                await asyncio.sleep(0.05)
                continue
            await self.consume_message(message)

    def stop(self) -> None:
        self._running = False

    async def consume_message(self, message: Any) -> None:
        payload = _extract_message_data(message)
        if not payload:
            return
        try:
            event = _parse_event(payload)
        except Exception:
            return
        await self.on_pattern_detected(event)

    async def on_pattern_detected(self, event: SkillUpdateEvent) -> int:
        candidates = await self._search_github(event)
        accepted = 0
        for candidate in list(candidates)[: self._TOP_CANDIDATES]:
            uri = _candidate_uri(candidate)
            if not uri:
                continue
            # Use pre-fetched content from candidate dict if available,
            # otherwise fall back to github_fetcher (backward compat with tests
            # that patch _search_github with uri-only dicts).
            content = ""
            if isinstance(candidate, dict):
                content = str(candidate.get("content") or "").strip()
            if not content:
                content = await self.github_fetcher.fetch(uri)
            if not content:
                continue
            # Use REST-based _validate() when expert_api_url is configured,
            # otherwise fall back to object-based _validation_score().
            if self.expert_api_url:
                score = await self._validate(content, event.domain)
            else:
                score = await self._validation_score(content=content, event=event, uri=uri)
            if score <= self._MIN_ACCEPT_SCORE:
                continue
            record = SkillRecord(
                skill_id=_candidate_skill_id(candidate, uri=uri),
                source=SkillSource.GITHUB,
                uri=uri,
                domains=_candidate_domains(candidate, event=event),
                dimensions=_candidate_dimensions(candidate, event=event),
                content=content,
                fetched_at=datetime.utcnow(),
                token_count=len(content) // 4,
            )
            await self.registry.register(record)
            accepted += 1
        return accepted

    async def _search_github(
        self, event: Optional[SkillUpdateEvent] = None
    ) -> List[Dict[str, Any]]:
        """
        Search GitHub for repositories containing SKILL.md matching event.domain.

        Uses GitHubClient for network I/O and BM25Scorer to rank and filter
        candidates.  Returns only candidates with BM25 score >= _MIN_ACCEPT_SCORE.
        Falls back to [] on any network / rate-limit error.
        """
        from babyai.skills.github_client import GitHubClient
        from babyai.skills.bm25_scorer import BM25Scorer, _MIN_ACCEPT_SCORE

        # Build search query: domain-scoped when event given, generic otherwise
        if event is not None:
            skill_query = f"language:markdown topic:{event.domain}"
        else:
            skill_query = ""

        # Placeholder event for frontmatter parsing
        _event_for_parse = event or SkillUpdateEvent(
            domain="general", query="", timestamp=datetime.utcnow()
        )
        _CONTENT_KEYWORDS = ("skill", "tool", "agent", "capability")

        client = GitHubClient()
        # Pass instance token via env override when set
        if self.github_token:
            os.environ.setdefault("GITHUB_TOKEN", self.github_token)

        repos = await client.search_skill_repos(skill_query, max_results=_MAX_RESULTS)
        if not repos:
            return []

        # Fetch SKILL.md content for all repos concurrently
        contents = await asyncio.gather(
            *[client.fetch_skill_md(r["full_name"]) for r in repos],
            return_exceptions=False,
        )

        # Build (repo, content) pairs — skip missing or too-short content
        pairs = []
        for repo, content in zip(repos, contents):
            if content is None:
                continue
            if len(content) < 100:
                continue
            lower = content.lower()
            if not any(kw in lower for kw in _CONTENT_KEYWORDS):
                continue
            pairs.append((repo, content))

        if not pairs:
            return []

        # Score with BM25 — query term is the event domain (or "skill" as fallback)
        bm25_query = (event.domain if event else None) or "skill"
        scorer      = BM25Scorer()
        scores      = scorer.score_many(bm25_query, [c for _, c in pairs])

        candidates: List[Dict[str, Any]] = []
        for (repo, content), score in zip(pairs, scores):
            if score < _MIN_ACCEPT_SCORE:
                continue
            repo_full_name = repo["full_name"]
            skill_id, domains, dimensions = _parse_skill_frontmatter(
                content, repo_full_name, _event_for_parse
            )
            candidates.append(
                {
                    "skill_id":   skill_id,
                    "uri":        f"{repo_full_name}/{SKILL_FILE_NAME}",
                    "content":    content,
                    "domains":    domains,
                    "dimensions": dimensions,
                    "score":      score,
                    "stars":      repo.get("stargazers_count", 0),
                }
            )

        # Sort by BM25 score descending
        candidates.sort(key=lambda c: c["score"], reverse=True)
        return candidates

    async def _fetch_skill_file(
        self, session: aiohttp.ClientSession, repo_full_name: str
    ) -> Optional[str]:
        for branch in ("main", "master"):
            url = f"{GITHUB_RAW_BASE}/{repo_full_name}/{branch}/{SKILL_FILE_NAME}"
            try:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.text()
            except Exception:
                continue
        return None

    async def fetch_skill(self, repo_url: str, skill_name: str) -> Optional[SkillManifest]:
        """Fetch a specific skill from a GitHub repo URL. Never crashes."""
        normalized = str(repo_url or "").strip().rstrip("/")
        # Extract owner/repo from full URL or bare "owner/repo"
        if "github.com/" in normalized:
            normalized = normalized.split("github.com/", 1)[-1].strip("/")
        parts = normalized.split("/")
        if len(parts) < 2:
            _log.warning("fetch_skill: cannot parse repo_url=%s", repo_url)
            return None
        owner, repo = parts[0], parts[1]
        repo_full = f"{owner}/{repo}"

        # Try skill_name/SKILL.md, then SKILL.md at root
        candidate_paths = [
            f"{skill_name}/SKILL.md",
            "SKILL.md",
        ]
        content = ""
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for branch in ("main", "master"):
                    for path in candidate_paths:
                        url = f"{GITHUB_RAW_BASE}/{repo_full}/{branch}/{path}"
                        try:
                            async with session.get(url) as resp:
                                if resp.status == 200:
                                    content = await resp.text()
                                    break
                        except Exception:
                            continue
                    if content:
                        break
        except Exception as exc:
            _log.warning("fetch_skill_failed repo=%s error=%s", repo_url, exc)
            return None

        if not content:
            return None

        skill_id = f"github-{owner}-{repo}-{sha256(repo_url.encode()).hexdigest()[:8]}"
        return SkillManifest(
            skill_id=skill_id,
            repo_url=repo_url,
            skill_name=skill_name,
            content=content,
        )

    def load_skill(self, manifest: SkillManifest) -> Any:
        """Dynamically import the skill entry_point. Tries pip install for missing packages. Never crashes."""
        for package in manifest.install_requires:
            try:
                importlib.import_module(package.replace("-", "_").split(">=")[0].split("==")[0].strip())
            except ImportError:
                try:
                    import subprocess
                    import sys
                    subprocess.check_call(
                        [sys.executable, "-m", "pip", "install", package],
                        capture_output=True,
                        timeout=30,
                    )
                    _log.info("pip_install_ok package=%s", package)
                except Exception as exc:
                    _log.warning("pip_install_failed package=%s error=%s", package, exc)

        if not manifest.entry_point:
            return None
        try:
            module_path, _, attr = manifest.entry_point.rpartition(".")
            if not module_path:
                return importlib.import_module(manifest.entry_point)
            mod = importlib.import_module(module_path)
            return getattr(mod, attr, mod)
        except Exception as exc:
            _log.warning("load_skill_failed skill_id=%s error=%s", manifest.skill_id, exc)
            return None

    async def _validate(self, content: str, domain: str) -> float:
        if not self.expert_api_url:
            return 0.0
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.expert_api_url}/validate",
                    json={"content": content, "domain": domain},
                ) as resp:
                    if resp.status != 200:
                        return 0.0
                    data = await resp.json(content_type=None)
                    return float(data.get("score", 0.0))
        except Exception:
            return 0.0

    async def _validation_score(self, *, content: str, event: SkillUpdateEvent, uri: str) -> float:
        clipped = content[: self._VALIDATION_CONTENT_CHARS]
        prompt = (
            "Assess skill quality for ingestion.\n"
            f"domain={event.domain}\n"
            f"dimension={event.dimension}\n"
            f"query={event.query}\n"
            f"uri={uri}\n\n"
            f"{clipped}"
        )
        try:
            raw = await self.expert_api.call_single(role="Validation", prompt=prompt)
        except Exception:
            return 0.0
        return _extract_score(raw)


def _extract_message_data(message: Any) -> str:
    if isinstance(message, Mapping):
        data = message.get("data")
        if data is None:
            return ""
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)
    if isinstance(message, bytes):
        return message.decode("utf-8", errors="replace")
    return str(message or "")


def _parse_event(raw: str) -> SkillUpdateEvent:
    payload = json.loads(raw)
    if hasattr(SkillUpdateEvent, "model_validate"):
        return SkillUpdateEvent.model_validate(payload)
    return SkillUpdateEvent.parse_obj(payload)


def _extract_score(payload: Any) -> float:
    if isinstance(payload, Mapping):
        for key in ("score", "confidence"):
            value = payload.get(key)
            numeric = _as_float(value)
            if numeric is not None:
                return numeric
        for nested_key in ("result", "data", "output"):
            nested = payload.get(nested_key)
            numeric = _extract_score(nested)
            if numeric > 0:
                return numeric
    return 0.0


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except Exception:
        return None


def _candidate_uri(candidate: Any) -> str:
    if isinstance(candidate, str):
        return candidate.strip()
    if isinstance(candidate, Mapping):
        return str(candidate.get("uri") or candidate.get("path") or "").strip()
    return ""


def _candidate_domains(candidate: Any, *, event: SkillUpdateEvent) -> List[str]:
    if isinstance(candidate, Mapping):
        raw = candidate.get("domains")
        if isinstance(raw, list):
            out = [str(item).strip() for item in raw if str(item).strip()]
            if out:
                return out
    return [str(event.domain or "general").strip() or "general"]


def _candidate_dimensions(candidate: Any, *, event: SkillUpdateEvent) -> List[str]:
    if isinstance(candidate, Mapping):
        raw = candidate.get("dimensions")
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
    dim = str(event.dimension or "").strip()
    return [dim] if dim else []


def _parse_skill_frontmatter(
    content: str, repo_full_name: str, event: "SkillUpdateEvent"
) -> tuple[str, list[str], list[str]]:
    """Extract skill_id, domains, dimensions from YAML frontmatter if present."""
    import yaml  # local import — pyyaml is a declared dependency

    skill_id = ""
    domains: list[str] = []
    dimensions: list[str] = []

    fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if fm_match:
        try:
            meta = yaml.safe_load(fm_match.group(1)) or {}
            skill_id = str(meta.get("skill_id") or "").strip()
            raw_domains = meta.get("domains", [])
            if isinstance(raw_domains, list):
                domains = [str(d).strip() for d in raw_domains if str(d).strip()]
            raw_dims = meta.get("dimensions", [])
            if isinstance(raw_dims, list):
                dimensions = [str(d).strip() for d in raw_dims if str(d).strip()]
        except Exception:
            pass

    if not skill_id:
        tail = repo_full_name.replace("/", "-")
        digest = sha256(repo_full_name.encode()).hexdigest()[:8]
        skill_id = f"github-{tail}-{digest}"
    if not domains:
        domains = [str(event.domain or "general").strip() or "general"]

    return skill_id, domains, dimensions


def _candidate_skill_id(candidate: Any, *, uri: str) -> str:
    if isinstance(candidate, Mapping):
        raw = str(candidate.get("skill_id") or "").strip()
        if raw:
            return raw
    digest = sha256(uri.encode("utf-8")).hexdigest()[:12]
    tail = uri.replace("\\", "/").strip("/").split("/")[-1] if uri else "github"
    stem = tail.rsplit(".", 1)[0] if "." in tail else tail
    base = stem or "github"
    return f"github-{base}-{digest}"

