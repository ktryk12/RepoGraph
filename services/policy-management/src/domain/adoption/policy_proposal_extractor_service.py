from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Sequence
import re

from babyai_shared.fingerprint import sha256_json


_SPLIT_RE = re.compile(r"(?<=[\.\!\?])\s+|\n+")
_SPACE_RE = re.compile(r"\s+")
_RULE_HINT_RE = re.compile(
    r"\b(must|should|require|required|forbid|forbidden|deny|allow|always|never|minimum|min|max|threshold|timeout|rollback|runbook|slo|monitoring)\b",
    flags=re.IGNORECASE,
)


class PolicyProposalExtractionError(ValueError):
    pass


@dataclass(frozen=True)
class PolicyCitation:
    source_ref: str
    quote: str
    locator: str | None = None

    def __post_init__(self) -> None:
        if not str(self.source_ref or "").strip():
            raise PolicyProposalExtractionError("citation.source_ref is required")
        if len(str(self.quote or "").strip()) < 8:
            raise PolicyProposalExtractionError("citation.quote must be >= 8 characters")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_ref": str(self.source_ref),
            "quote": str(self.quote),
            "locator": (str(self.locator) if isinstance(self.locator, str) and self.locator.strip() else None),
        }


@dataclass(frozen=True)
class PolicyChangeCandidate:
    statement: str
    citations: List[PolicyCitation]

    def __post_init__(self) -> None:
        if len(str(self.statement or "").strip()) < 8:
            raise PolicyProposalExtractionError("change.statement must be >= 8 characters")
        if not self.citations:
            raise PolicyProposalExtractionError("change must include at least one citation")

    def to_dict(self, *, change_id: str) -> Dict[str, Any]:
        return {
            "change_id": str(change_id),
            "op": "add_rule",
            "statement": str(self.statement),
            "citations": [item.to_dict() for item in self.citations],
        }


class PolicyProposalExtractorService:
    """
    Deterministic policy proposal extractor with mandatory citations.

    Intended inputs:
    - PDF chunks (locator like "page:12")
    - YT transcript windows (locator like "ts:00:04:10")
    """

    def __init__(
        self,
        *,
        max_changes: int = 12,
        min_statement_chars: int = 24,
        policy_name: str = "ingest_policy",
    ) -> None:
        self._max_changes = max(1, int(max_changes))
        self._min_statement_chars = max(8, int(min_statement_chars))
        self._default_policy_name = str(policy_name or "ingest_policy")

    def extract_from_text(
        self,
        *,
        text: str,
        source_ref: str,
        source_type: str,
        policy_name: str | None = None,
        uri: str | None = None,
        namespace: str | None = None,
        rights_label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        raw = str(text or "")
        segments: List[Dict[str, Any]] = []
        for line_no, part in enumerate(_SPLIT_RE.split(raw), start=1):
            normalized = _normalize_text(part)
            if not normalized:
                continue
            segments.append({"text": normalized, "locator": f"line:{line_no}"})
        return self.extract_from_segments(
            segments=segments,
            source_ref=source_ref,
            source_type=source_type,
            policy_name=policy_name,
            uri=uri,
            namespace=namespace,
            rights_label=rights_label,
            metadata=metadata,
        )

    def extract_from_segments(
        self,
        *,
        segments: Sequence[Mapping[str, Any] | Dict[str, Any]],
        source_ref: str,
        source_type: str,
        policy_name: str | None = None,
        uri: str | None = None,
        namespace: str | None = None,
        rights_label: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        source_ref_text = _required_text(source_ref, name="source_ref")
        source_type_text = _required_text(source_type, name="source_type")
        policy = _required_text(policy_name or self._default_policy_name, name="policy_name")
        source_payload = {
            "schema_version": 1,
            "source_ref": source_ref_text,
            "source_type": source_type_text,
            "uri": _optional_text(uri),
            "namespace": _optional_text(namespace),
            "snapshot_id": None,
            "rights_label": _optional_text(rights_label),
            "metadata": {},
        }

        candidates = self._extract_candidates(segments=segments, source_ref=source_ref_text)
        if not candidates:
            raise PolicyProposalExtractionError("no_policy_statements_with_citations")
        if len(candidates) > self._max_changes:
            candidates = candidates[: self._max_changes]

        proposal_id = f"policy-proposal-{sha256_json([item.statement for item in candidates])[:16]}"
        changes: List[Dict[str, Any]] = []
        claims: List[Dict[str, Any]] = []
        for idx, candidate in enumerate(candidates, start=1):
            change_id = f"change-{idx:03d}"
            changes.append(candidate.to_dict(change_id=change_id))
            claims.append(
                {
                    "change_id": change_id,
                    "claim": candidate.statement,
                    "citations": [item.to_dict() for item in candidate.citations],
                }
            )

        citation_count = sum(len(item.citations) for item in candidates)
        evidence_pack = {
            "schema_version": 1,
            "pack_id": f"evidence-{proposal_id}",
            "evidence_refs": [source_ref_text],
            "source_refs": [dict(source_payload)],
            "claims": claims,
            "metadata": {
                "citation_count": int(citation_count),
                "citations_mandatory": True,
            },
        }
        payload = {
            "schema_version": 1,
            "proposal_id": proposal_id,
            "policy_name": policy,
            "changes": changes,
            "source_refs": [dict(source_payload)],
            "rationale": f"Extracted {len(changes)} policy changes with mandatory citations.",
            "risk_report": {},
            "evidence_pack": evidence_pack,
            "metadata": {
                "citations_required": True,
                "citation_count": int(citation_count),
                "segment_count": int(len(segments)),
                **{str(k): v for k, v in dict(metadata or {}).items()},
            },
        }
        self._validate_citations(payload)
        return payload

    def _extract_candidates(
        self,
        *,
        segments: Sequence[Mapping[str, Any] | Dict[str, Any]],
        source_ref: str,
    ) -> List[PolicyChangeCandidate]:
        dedupe: Dict[str, PolicyChangeCandidate] = {}
        for raw in segments:
            if not isinstance(raw, Mapping):
                continue
            statement = _normalize_text(raw.get("text"))
            if len(statement) < self._min_statement_chars:
                continue
            if not _RULE_HINT_RE.search(statement):
                continue
            locator = _optional_text(raw.get("locator"))
            citation = PolicyCitation(
                source_ref=source_ref,
                quote=_truncate(statement, 220),
                locator=locator,
            )
            key = statement.lower()
            if key in dedupe:
                continue
            dedupe[key] = PolicyChangeCandidate(statement=statement, citations=[citation])
        return [dedupe[key] for key in sorted(dedupe.keys())]

    def _validate_citations(self, payload: Mapping[str, Any]) -> None:
        changes = payload.get("changes")
        if not isinstance(changes, list) or not changes:
            raise PolicyProposalExtractionError("proposal.changes must not be empty")
        for index, item in enumerate(changes):
            if not isinstance(item, Mapping):
                raise PolicyProposalExtractionError(f"proposal.changes[{index}] must be an object")
            citations = item.get("citations")
            if not isinstance(citations, list) or not citations:
                raise PolicyProposalExtractionError(f"proposal.changes[{index}].citations must not be empty")
            for cidx, citation in enumerate(citations):
                if not isinstance(citation, Mapping):
                    raise PolicyProposalExtractionError(
                        f"proposal.changes[{index}].citations[{cidx}] must be an object"
                    )
                if not _optional_text(citation.get("source_ref")):
                    raise PolicyProposalExtractionError(
                        f"proposal.changes[{index}].citations[{cidx}].source_ref is required"
                    )
                if len(str(citation.get("quote") or "").strip()) < 8:
                    raise PolicyProposalExtractionError(
                        f"proposal.changes[{index}].citations[{cidx}].quote must be >= 8 characters"
                    )


_SERVICE: PolicyProposalExtractorService | None = None


def get_policy_proposal_extractor_service(*, reload: bool = False) -> PolicyProposalExtractorService:
    global _SERVICE
    if _SERVICE is None or reload:
        _SERVICE = PolicyProposalExtractorService()
    return _SERVICE


def _required_text(value: Any, *, name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise PolicyProposalExtractionError(f"{name} must be non-empty")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _normalize_text(value: Any) -> str:
    text = _optional_text(value) or ""
    if not text:
        return ""
    return _SPACE_RE.sub(" ", text).strip()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    suffix = "..."
    keep = max(0, limit - len(suffix))
    return value[:keep].rstrip() + suffix
