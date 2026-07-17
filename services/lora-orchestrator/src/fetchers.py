from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, List
from uuid import uuid4

from babyai.skills.fetchers.github_fetcher import GitHubSkillFetcher
from babyai.skills.fetchers.huggingface_fetcher import HuggingFaceSkillFetcher

from .models import AdapterCandidate


class LoRAFetcher:
    _MAX_RESULTS = 5
    _MAX_AGE_DAYS = 183
    _ALLOWED_LICENSES = {"apache-2.0", "mit", "cc-by-4.0"}

    def __init__(
        self,
        *,
        github_fetcher: Any | None = None,
        huggingface_fetcher: Any | None = None,
    ) -> None:
        self.github_fetcher = github_fetcher or GitHubSkillFetcher()
        self.huggingface_fetcher = huggingface_fetcher or HuggingFaceSkillFetcher()

    async def search(self, domain: str) -> list[AdapterCandidate]:
        gh_candidates = await self._search_source(self.github_fetcher, str(domain), source="github")
        hf_candidates = await self._search_source(self.huggingface_fetcher, str(domain), source="huggingface")
        candidates = self._deduplicate(gh_candidates + hf_candidates)
        filtered = [row for row in candidates if self._is_fresh(row) and self._is_allowed_license(row)]
        filtered = [row for row in filtered if self._looks_like_lora_candidate(row)]
        return sorted(filtered, key=lambda item: item.last_updated, reverse=True)[: self._MAX_RESULTS]

    async def collect_examples(self, domain: str) -> list[str]:
        return [
            f"domain={domain} example policy snippet {idx}"
            for idx in range(1, 201)
        ]

    async def _search_source(self, fetcher: Any, domain: str, *, source: str) -> list[AdapterCandidate]:
        search = getattr(fetcher, "search", None)
        if not callable(search):
            return []
        raw = search(domain)
        if hasattr(raw, "__await__"):
            raw = await raw
        if not isinstance(raw, list):
            return []
        out: list[AdapterCandidate] = []
        for row in raw:
            candidate = _to_candidate(row, source=source)
            if candidate is None:
                continue
            out.append(candidate)
        return out

    def _deduplicate(self, candidates: Iterable[AdapterCandidate]) -> list[AdapterCandidate]:
        seen: set[str] = set()
        out: list[AdapterCandidate] = []
        for candidate in candidates:
            key = str(candidate.source_url).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(candidate)
        return out

    def _is_fresh(self, candidate: AdapterCandidate) -> bool:
        updated = _to_utc(candidate.last_updated)
        return (datetime.now(timezone.utc) - updated) <= timedelta(days=self._MAX_AGE_DAYS)

    def _is_allowed_license(self, candidate: AdapterCandidate) -> bool:
        value = str(candidate.license or "").strip().lower()
        if not value:
            return False
        if "huggingface.co" in str(candidate.source_url).lower():
            return value in self._ALLOWED_LICENSES
        return True

    def _looks_like_lora_candidate(self, candidate: AdapterCandidate) -> bool:
        text = " ".join(
            [
                str(candidate.source_url or ""),
                str(candidate.base_model or ""),
                str(candidate.candidate_id or ""),
            ]
        ).lower()
        return ("lora" in text) or ("peft" in text)


def _to_candidate(payload: Any, *, source: str) -> AdapterCandidate | None:
    if isinstance(payload, AdapterCandidate):
        return payload
    if not isinstance(payload, dict):
        return None
    source_url = str(payload.get("source_url") or payload.get("url") or "").strip()
    if not source_url:
        return None
    candidate_id = str(payload.get("candidate_id") or payload.get("id") or f"{source}-{uuid4().hex[:8]}")
    license_value = str(payload.get("license") or "").strip() or "unknown"
    base_model = str(payload.get("base_model") or payload.get("model") or "").strip()
    param_count = int(payload.get("param_count") or payload.get("params") or 0)
    last_updated = _to_datetime(payload.get("last_updated"))
    file_path = Path(str(payload.get("file_path") or f"artifacts/lora/{candidate_id}.safetensors"))
    fmt_raw = str(payload.get("file_format") or file_path.suffix.lstrip(".") or "other").lower()
    file_format = "other"
    if fmt_raw == "safetensors":
        file_format = "safetensors"
    elif fmt_raw == "pickle":
        file_format = "pickle"
    return AdapterCandidate(
        candidate_id=candidate_id,
        source_url=source_url,
        license=license_value,
        base_model=base_model,
        param_count=param_count,
        last_updated=last_updated,
        file_path=file_path,
        file_format=file_format,  # type: ignore[arg-type]
    )


def _to_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return _to_utc(value)
    text = str(value or "").strip()
    if not text:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)
    return _to_utc(parsed)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
