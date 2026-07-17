from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping


_CONTENT_KEYS = {"content", "excerpt", "text", "body", "raw", "payload"}
_CANDIDATE_KEYS = {
    "candidate_id",
    "source_ref",
    "source_type",
    "namespace",
    "path",
    "uri",
    "score",
    "rights_label",
    "rights_basis",
    "tags",
    "metadata",
}


@dataclass(frozen=True)
class DiscoveryCandidate:
    candidate_id: str
    source_ref: str
    rights_label: str
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": str(self.candidate_id),
            "source_ref": str(self.source_ref),
            "rights_label": str(self.rights_label),
            "metadata": dict(self.metadata),
        }


class DiscoveryService:
    """
    Extracts discovery candidates as metadata-only records.

    Any content-bearing fields are stripped by default.
    """

    def extract_candidates(self, request: Mapping[str, Any]) -> List[Dict[str, Any]]:
        raw_candidates = self._raw_candidates(request)
        out: List[Dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for idx, raw in enumerate(raw_candidates):
            if not isinstance(raw, Mapping):
                continue
            candidate = self._normalize_candidate(raw, index=idx)
            if candidate is None:
                continue
            dedupe_key = (candidate.candidate_id, candidate.source_ref)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append(candidate.to_dict())
        return out

    def _raw_candidates(self, request: Mapping[str, Any]) -> List[Mapping[str, Any]]:
        rows: List[Mapping[str, Any]] = []
        direct = request.get("discovery_candidates")
        if isinstance(direct, list):
            rows.extend([item for item in direct if isinstance(item, Mapping)])

        alt = request.get("candidates")
        if isinstance(alt, list):
            rows.extend([item for item in alt if isinstance(item, Mapping)])

        pack = request.get("pack")
        if isinstance(pack, Mapping):
            metadata = pack.get("metadata")
            if isinstance(metadata, Mapping):
                nested = metadata.get("candidates")
                if isinstance(nested, list):
                    rows.extend([item for item in nested if isinstance(item, Mapping)])
        return rows

    def _normalize_candidate(self, raw: Mapping[str, Any], *, index: int) -> DiscoveryCandidate | None:
        source_ref = _non_empty(raw.get("source_ref")) or _non_empty(raw.get("path")) or _non_empty(raw.get("uri"))
        candidate_id = _non_empty(raw.get("candidate_id")) or source_ref or f"candidate-{index + 1}"
        if not candidate_id:
            return None
        rights_label = _non_empty(raw.get("rights_label")) or _non_empty(raw.get("rights")) or "unknown"
        metadata = _sanitize_metadata(raw)
        nested = metadata.pop("metadata", None)
        if isinstance(nested, Mapping):
            for key, value in nested.items():
                name = str(key).strip()
                if not name or name in metadata:
                    continue
                metadata[name] = value
        metadata.pop("candidate_id", None)
        metadata.pop("source_ref", None)
        metadata.pop("rights_label", None)
        metadata.setdefault("candidate_id", candidate_id)
        metadata.setdefault("source_ref", source_ref or "")
        metadata.setdefault("rights_label", rights_label.lower())
        return DiscoveryCandidate(
            candidate_id=str(candidate_id),
            source_ref=str(source_ref or ""),
            rights_label=str(rights_label).lower(),
            metadata=metadata,
        )


def get_discovery_service() -> DiscoveryService:
    return DiscoveryService()


def _sanitize_metadata(raw: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        lowered = name.lower()
        if lowered in _CONTENT_KEYS:
            continue
        if lowered not in _CANDIDATE_KEYS:
            continue
        if lowered == "metadata" and isinstance(value, Mapping):
            out["metadata"] = _sanitize_nested_metadata(value)
            continue
        if lowered == "tags" and isinstance(value, list):
            out["tags"] = [str(item) for item in value if str(item).strip()]
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[name] = value
    return out


def _sanitize_nested_metadata(raw: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        if name.lower() in _CONTENT_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[name] = value
    return out


def _non_empty(value: Any) -> str:
    text = str(value or "").strip()
    return text
