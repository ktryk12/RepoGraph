from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Sequence
import math

from babyai_shared.fingerprint import sha256_json


DEFAULT_TAG_WEIGHTS: Dict[str, float] = {
    "python": 1.0,
    "ml": 0.8,
    "rag": 0.7,
    "infra": 0.6,
    "security": 0.9,
    "testing": 0.5,
}


@dataclass(frozen=True)
class GitScoutCandidate:
    repo_id: str
    repo_url: str
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "repo_id", _required_text(self.repo_id, name="repo_id"))
        object.__setattr__(self, "repo_url", _required_text(self.repo_url, name="repo_url"))
        object.__setattr__(self, "tags", _normalize_tags(self.tags))
        object.__setattr__(self, "metadata", _as_dict(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "repo_url": self.repo_url,
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GitScoutCandidate":
        if not isinstance(payload, Mapping):
            raise ValueError("candidate payload must be object")
        repo_id = payload.get("repo_id")
        if repo_id is None:
            repo_id = payload.get("name")
        repo_url = payload.get("repo_url")
        if repo_url is None:
            repo_url = payload.get("url")
        return cls(
            repo_id=str(repo_id or ""),
            repo_url=str(repo_url or ""),
            tags=_normalize_tags(payload.get("tags")),
            metadata=_as_dict(payload.get("metadata")),
        )


@dataclass(frozen=True)
class GitScoutRecommendation:
    repo_id: str
    repo_url: str
    score: float
    rationale: List[str]
    tags: List[str]
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "repo_url": self.repo_url,
            "score": float(self.score),
            "rationale": list(self.rationale),
            "tags": list(self.tags),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class GitScoutResult:
    total_candidates: int
    considered_candidates: int
    shortlisted_count: int
    shortlist: List[GitScoutRecommendation]
    shortlist_fingerprint: str
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_candidates": int(self.total_candidates),
            "considered_candidates": int(self.considered_candidates),
            "shortlisted_count": int(self.shortlisted_count),
            "shortlist": [item.to_dict() for item in self.shortlist],
            "shortlist_fingerprint": self.shortlist_fingerprint,
            "metadata": dict(self.metadata),
        }


class GitScoutService:
    """
    Rank curated repos into a shortlist with deterministic rationale.
    """

    def __init__(
        self,
        *,
        tag_weights: Mapping[str, float] | None = None,
        max_candidates: int = 1_000,
        default_top_k: int = 20,
    ) -> None:
        self._tag_weights = _normalize_weights(tag_weights or DEFAULT_TAG_WEIGHTS)
        self._max_candidates = max(1, int(max_candidates))
        self._default_top_k = max(1, int(default_top_k))

    def shortlist(
        self,
        candidates: Sequence[GitScoutCandidate | Mapping[str, Any]],
        *,
        allowlist: Iterable[str] | None = None,
        required_tags: Iterable[str] | None = None,
        top_k: int | None = None,
        tag_weight_overrides: Mapping[str, float] | None = None,
    ) -> GitScoutResult:
        normalized = self._normalize_candidates(candidates)
        allow = _normalize_token_set(allowlist)
        required = _normalize_token_set(required_tags)
        effective_weights = dict(self._tag_weights)
        if isinstance(tag_weight_overrides, Mapping):
            effective_weights.update(_normalize_weights(tag_weight_overrides))

        filtered: List[GitScoutCandidate] = []
        for candidate in normalized:
            if allow and (candidate.repo_id not in allow and candidate.repo_url not in allow):
                continue
            if required and not required.issubset(set(candidate.tags)):
                continue
            filtered.append(candidate)

        recommendations: List[GitScoutRecommendation] = []
        for candidate in filtered:
            score, rationale = _score_candidate(candidate, tag_weights=effective_weights)
            recommendations.append(
                GitScoutRecommendation(
                    repo_id=candidate.repo_id,
                    repo_url=candidate.repo_url,
                    score=score,
                    rationale=rationale,
                    tags=list(candidate.tags),
                    metadata=dict(candidate.metadata),
                )
            )
        recommendations.sort(key=lambda item: (-float(item.score), str(item.repo_id), str(item.repo_url)))
        limit = max(1, int(top_k if isinstance(top_k, int) else self._default_top_k))
        shortlist = recommendations[:limit]
        shortlist_fingerprint = sha256_json([item.to_dict() for item in shortlist])
        return GitScoutResult(
            total_candidates=len(normalized),
            considered_candidates=len(filtered),
            shortlisted_count=len(shortlist),
            shortlist=shortlist,
            shortlist_fingerprint=shortlist_fingerprint,
            metadata={
                "tag_weights": dict(sorted(effective_weights.items(), key=lambda row: row[0])),
                "required_tags": sorted(required),
                "allowlist_enabled": bool(allow),
            },
        )

    def _normalize_candidates(
        self,
        candidates: Sequence[GitScoutCandidate | Mapping[str, Any]],
    ) -> List[GitScoutCandidate]:
        if not isinstance(candidates, Sequence):
            raise ValueError("candidates must be a sequence")
        rows: List[GitScoutCandidate] = []
        seen = set()
        for raw in candidates:
            item = raw if isinstance(raw, GitScoutCandidate) else GitScoutCandidate.from_dict(raw)
            key = (item.repo_id, item.repo_url)
            if key in seen:
                continue
            seen.add(key)
            rows.append(item)
        rows.sort(key=lambda row: (row.repo_id, row.repo_url))
        return rows[: self._max_candidates]


def _score_candidate(
    candidate: GitScoutCandidate,
    *,
    tag_weights: Mapping[str, float],
) -> tuple[float, List[str]]:
    score = 0.0
    rationale: List[str] = []

    stars = _to_float(candidate.metadata.get("stars"), default=0.0)
    if stars > 0:
        stars_score = math.log10(1.0 + stars)
        score += stars_score
        rationale.append(f"stars:{int(stars)}(+{stars_score:.3f})")

    quality = _to_float(candidate.metadata.get("quality_score"), default=0.0)
    if quality > 0:
        score += quality
        rationale.append(f"quality_score:{quality:.3f}(+{quality:.3f})")

    recent_days = _to_float(candidate.metadata.get("recent_commit_days"), default=3650.0)
    freshness = max(0.0, 1.0 - min(recent_days, 3650.0) / 3650.0)
    if freshness > 0:
        score += freshness
        rationale.append(f"freshness_days:{recent_days:.0f}(+{freshness:.3f})")

    for tag in candidate.tags:
        weight = _to_float(tag_weights.get(tag), default=0.0)
        if weight == 0.0:
            continue
        score += weight
        rationale.append(f"tag:{tag}(+{weight:.3f})")

    if not rationale:
        rationale.append("baseline:0.000")
    return float(score), rationale


def _normalize_weights(value: Mapping[str, float]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for key, raw in dict(value).items():
        name = str(key or "").strip().lower()
        if not name:
            continue
        out[name] = _to_float(raw, default=0.0)
    return out


def _normalize_token_set(value: Iterable[str] | None) -> set[str]:
    if value is None:
        return set()
    out: set[str] = set()
    for raw in value:
        token = str(raw or "").strip()
        if token:
            out.add(token)
    return out


def _normalize_tags(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen = set()
    for raw in value:
        tag = str(raw or "").strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
    out.sort()
    return out


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _required_text(value: Any, *, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _to_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)

