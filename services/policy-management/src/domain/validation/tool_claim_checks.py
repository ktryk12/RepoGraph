from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple
import json
import re

from babyai_shared.fingerprint import canonical_json, sha256_bytes
from babyai_shared.storage.artifact_store import FileArtifactStore
from tools.contracts import TOOL_EVIDENCE_PACK, ToolEvidencePack, validate_tool_contract


CLAIM_TO_TOOL_ID: Dict[str, str] = {
    "tests_passed": "run_tests",
    "lint_clean": "run_lint",
    "build_succeeded": "run_build",
    "coverage_reported": "coverage",
}
_KNOWN_CLAIMS = set(CLAIM_TO_TOOL_ID.keys())
_KNOWN_TOOL_IDS = set(CLAIM_TO_TOOL_ID.values())

_PROSE_PATTERNS: Dict[str, List[re.Pattern[str]]] = {
    "tests_passed": [
        re.compile(r"\ball tests?\s+(pass|passed|passing)\b", re.IGNORECASE),
        re.compile(r"\b(unit|integration|e2e)\s+tests?\s+(pass|passed|passing)\b", re.IGNORECASE),
        re.compile(r"\btests?\s+(are\s+)?green\b", re.IGNORECASE),
        re.compile(r"\bpipeline\s+(is\s+)?passing\b", re.IGNORECASE),
        re.compile(r"\ball checks?\s+are\s+green\b", re.IGNORECASE),
    ],
    "lint_clean": [
        re.compile(r"\blint\s+(is\s+)?(clean|pass|passed|passing|green)\b", re.IGNORECASE),
        re.compile(r"\bno lint (errors|issues)\b", re.IGNORECASE),
        re.compile(r"\bformatter applied cleanly\b", re.IGNORECASE),
    ],
    "build_succeeded": [
        re.compile(r"\bbuild\s+(succeeded|successful|pass|passed|passing)\b", re.IGNORECASE),
        re.compile(r"\bcompil(ation|e)\s+(succeeded|successful|pass|passed|passing)\b", re.IGNORECASE),
    ],
    "coverage_reported": [
        re.compile(r"\bcoverage\b.{0,24}\b\d{1,3}%\b", re.IGNORECASE),
        re.compile(r"\bcoverage (improved|increase|reported)\b", re.IGNORECASE),
        re.compile(r"\bmeets coverage threshold\b", re.IGNORECASE),
    ],
}


@dataclass(frozen=True)
class ToolClaimFailure:
    tag: str
    message: str
    evidence_ref: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tag": str(self.tag),
            "message": str(self.message),
            "evidence_ref": str(self.evidence_ref) if isinstance(self.evidence_ref, str) and self.evidence_ref else None,
        }


@dataclass(frozen=True)
class _EvidenceResolution:
    pack: ToolEvidencePack | None
    failures: List[ToolClaimFailure]
    evidence_ref: str | None


def check_tool_claim_failures(
    decision: Dict[str, Any],
    *,
    artifact_root: str | Path = "artifacts",
) -> List[ToolClaimFailure]:
    failures: List[ToolClaimFailure] = []

    prose_claims = _detect_prose_claims(decision)
    has_structured = "tool_claims" in decision
    structured_claims, structured_failures = _parse_structured_claims(
        decision.get("tool_claims"),
        prose_claims=prose_claims,
        has_structured=has_structured,
    )
    failures.extend(structured_failures)

    required_tool_ids = _required_tool_ids(prose_claims, structured_claims)
    if required_tool_ids:
        failures.extend(
            _enforce_tool_evidence(
                decision=decision,
                required_tool_ids=required_tool_ids,
                artifact_root=artifact_root,
            )
        )

    return _dedupe_failures(failures)


def _detect_prose_claims(decision: Dict[str, Any]) -> Dict[str, List[str]]:
    claims: Dict[str, List[str]] = {}
    for text, path in _collect_claim_texts(decision):
        low = text.strip()
        if not low:
            continue
        for claim, patterns in _PROSE_PATTERNS.items():
            if any(p.search(low) for p in patterns):
                claims.setdefault(claim, [])
                claims[claim].append(path)
    return claims


def _collect_claim_texts(decision: Dict[str, Any]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    _scan_any(decision.get("verification_plan"), "$.verification_plan", out)
    _scan_any(decision.get("stop_conditions"), "$.stop_conditions", out)
    _scan_any(decision.get("rationale"), "$.rationale", out)
    _scan_any(decision.get("risks"), "$.risks", out)
    _scan_any(decision.get("ops_readiness"), "$.ops_readiness", out)
    return out


def _scan_any(value: Any, path: str, out: List[Tuple[str, str]]) -> None:
    if isinstance(value, str):
        txt = value.strip()
        if txt:
            out.append((txt, path))
        return
    if isinstance(value, list):
        for idx, item in enumerate(value):
            _scan_any(item, f"{path}[{idx}]", out)
        return
    if isinstance(value, dict):
        for key in sorted(value.keys()):
            _scan_any(value.get(key), f"{path}.{key}", out)


def _parse_structured_claims(
    raw_claims: Any,
    *,
    prose_claims: Mapping[str, List[str]],
    has_structured: bool,
) -> Tuple[List[Dict[str, str]], List[ToolClaimFailure]]:
    failures: List[ToolClaimFailure] = []
    parsed: List[Dict[str, str]] = []

    if not has_structured:
        return parsed, failures

    if not isinstance(raw_claims, list):
        failures.append(
            ToolClaimFailure(
                tag="tool_claims_invalid",
                message="decision.tool_claims must be a list when present",
                evidence_ref="$.tool_claims",
            )
        )
        return parsed, failures

    if prose_claims and len(raw_claims) == 0:
        failures.append(
            ToolClaimFailure(
                tag="tool_claims_empty_with_prose_claims",
                message="decision.tool_claims must not be empty when prose tool claims are detected",
                evidence_ref="$.tool_claims",
            )
        )
        return parsed, failures

    for idx, item in enumerate(raw_claims):
        path = f"$.tool_claims[{idx}]"
        if not isinstance(item, dict):
            failures.append(
                ToolClaimFailure(
                    tag="tool_claims_invalid_item",
                    message="tool_claims entries must be objects",
                    evidence_ref=path,
                )
            )
            continue

        claim = str(item.get("claim") or "").strip()
        tool_id = str(item.get("tool_id") or "").strip()
        if not claim:
            failures.append(
                ToolClaimFailure(
                    tag="tool_claim_missing_claim",
                    message="tool_claims[].claim is required",
                    evidence_ref=f"{path}.claim",
                )
            )
            continue
        if claim not in _KNOWN_CLAIMS:
            failures.append(
                ToolClaimFailure(
                    tag="tool_claim_unknown",
                    message=f"unknown tool claim '{claim}'",
                    evidence_ref=f"{path}.claim",
                )
            )
            continue
        if not tool_id:
            failures.append(
                ToolClaimFailure(
                    tag="tool_claim_missing_tool_id",
                    message=f"tool_claims[].tool_id is required for claim '{claim}'",
                    evidence_ref=f"{path}.tool_id",
                )
            )
            continue
        if tool_id not in _KNOWN_TOOL_IDS:
            failures.append(
                ToolClaimFailure(
                    tag="tool_claim_unknown_tool_id",
                    message=f"unknown tool_id '{tool_id}' for claim '{claim}'",
                    evidence_ref=f"{path}.tool_id",
                )
            )
            continue
        expected_tool = CLAIM_TO_TOOL_ID.get(claim)
        if expected_tool and tool_id != expected_tool:
            failures.append(
                ToolClaimFailure(
                    tag="tool_claim_tool_mismatch",
                    message=f"claim '{claim}' expects tool_id '{expected_tool}', got '{tool_id}'",
                    evidence_ref=path,
                )
            )
            continue

        parsed.append({"claim": claim, "tool_id": tool_id})

    prose_set = set(str(x) for x in prose_claims.keys())
    structured_set = {str(item["claim"]) for item in parsed}
    missing_from_structured = sorted(prose_set - structured_set)
    for claim in missing_from_structured:
        failures.append(
            ToolClaimFailure(
                tag=f"tool_claims_missing_prose_claim:{claim}",
                message=f"prose claim '{claim}' is not represented in decision.tool_claims",
                evidence_ref="$.tool_claims",
            )
        )

    return parsed, failures


def _required_tool_ids(
    prose_claims: Mapping[str, List[str]],
    structured_claims: Iterable[Dict[str, str]],
) -> List[str]:
    required: List[str] = []
    for claim in prose_claims.keys():
        tool_id = CLAIM_TO_TOOL_ID.get(str(claim))
        if tool_id:
            required.append(tool_id)
    for item in structured_claims:
        tool_id = str(item.get("tool_id") or "").strip()
        if tool_id:
            required.append(tool_id)
    seen = set()
    out: List[str] = []
    for tool_id in required:
        if tool_id in seen:
            continue
        seen.add(tool_id)
        out.append(tool_id)
    return out


def _enforce_tool_evidence(
    *,
    decision: Dict[str, Any],
    required_tool_ids: List[str],
    artifact_root: str | Path,
) -> List[ToolClaimFailure]:
    failures: List[ToolClaimFailure] = []
    resolution = _resolve_tool_evidence_pack(decision=decision, artifact_root=artifact_root)
    failures.extend(list(resolution.failures))
    evidence_pack = resolution.pack
    if evidence_pack is None:
        if not failures:
            failures.append(
                ToolClaimFailure(
                    tag="tool_claim_without_evidence",
                    message="tool claim detected but no valid tool evidence pack was provided",
                    evidence_ref="$.tool_evidence_pack",
                )
            )
        return failures

    evidence_ref = str(resolution.evidence_ref or "").strip() or "$.tool_evidence_pack"
    if not evidence_ref.startswith("artifact:sha256:"):
        failures.append(
            ToolClaimFailure(
                tag="tool_claim_without_evidence",
                message="tool claim detected but no evidence fingerprint binding was provided",
                evidence_ref="$.tool_evidence_ref",
            )
        )

    status_by_tool: Dict[str, List[bool]] = {}
    for item in evidence_pack.tool_results:
        status_by_tool.setdefault(str(item.tool_id), []).append(bool(item.ok))

    for tool_id in required_tool_ids:
        states = status_by_tool.get(str(tool_id), [])
        if not states:
            failures.append(
                ToolClaimFailure(
                    tag=f"tool_evidence_missing_tool_run:{tool_id}",
                    message=f"tool evidence pack is missing required tool run '{tool_id}'",
                    evidence_ref=evidence_ref,
                )
            )
            continue
        if not any(states):
            failures.append(
                ToolClaimFailure(
                    tag=f"tool_run_failed:{tool_id}",
                    message=f"tool run '{tool_id}' exists but did not succeed",
                    evidence_ref=evidence_ref,
                )
            )

    return failures


def _resolve_tool_evidence_pack(
    *,
    decision: Dict[str, Any],
    artifact_root: str | Path,
) -> _EvidenceResolution:
    failures: List[ToolClaimFailure] = []
    inline_pack = decision.get("tool_evidence_pack")
    evidence_ref = str(decision.get("tool_evidence_ref") or "").strip() or None

    if inline_pack is not None and not evidence_ref:
        failures.append(
            ToolClaimFailure(
                tag="tool_claim_without_evidence",
                message="tool_evidence_ref is required to fingerprint-bind tool evidence",
                evidence_ref="$.tool_evidence_ref",
            )
        )
        return _EvidenceResolution(pack=None, failures=failures, evidence_ref=None)

    if isinstance(inline_pack, dict):
        try:
            validate_tool_contract(TOOL_EVIDENCE_PACK, inline_pack)
        except Exception as exc:
            failures.append(
                ToolClaimFailure(
                    tag="tool_evidence_corrupt",
                    message=f"inline tool evidence pack is invalid: {exc}",
                    evidence_ref="$.tool_evidence_pack",
                )
            )
            return _EvidenceResolution(pack=None, failures=failures, evidence_ref=evidence_ref)

        if evidence_ref:
            expected = _artifact_ref_digest(evidence_ref)
            actual = sha256_bytes(canonical_json(inline_pack).encode("utf-8"))
            if expected and actual != expected:
                failures.append(
                    ToolClaimFailure(
                        tag="tool_evidence_fingerprint_mismatch",
                        message="tool_evidence_ref hash does not match inline tool_evidence_pack payload",
                        evidence_ref="$.tool_evidence_ref",
                    )
                )
                return _EvidenceResolution(pack=None, failures=failures, evidence_ref=evidence_ref)
        return _EvidenceResolution(
            pack=ToolEvidencePack.from_dict(inline_pack),
            failures=failures,
            evidence_ref=evidence_ref,
        )

    ref = str(evidence_ref or "").strip()
    if not ref:
        return _EvidenceResolution(pack=None, failures=failures, evidence_ref=None)

    try:
        payload = _load_pack_from_ref(ref, artifact_root=artifact_root, failures=failures)
        if payload is None:
            return _EvidenceResolution(pack=None, failures=failures, evidence_ref=ref)
        validate_tool_contract(TOOL_EVIDENCE_PACK, payload)
        return _EvidenceResolution(
            pack=ToolEvidencePack.from_dict(payload),
            failures=failures,
            evidence_ref=ref,
        )
    except Exception as exc:
        failures.append(
            ToolClaimFailure(
                tag="tool_evidence_corrupt",
                message=f"tool evidence payload is invalid: {exc}",
                evidence_ref="$.tool_evidence_ref",
            )
        )
        return _EvidenceResolution(pack=None, failures=failures, evidence_ref=ref)


def _load_pack_from_ref(
    ref: str,
    *,
    artifact_root: str | Path,
    failures: List[ToolClaimFailure],
) -> Dict[str, Any] | None:
    store = FileArtifactStore(root=artifact_root)
    raw = store.get(str(ref))
    if raw is None:
        failures.append(
            ToolClaimFailure(
                tag="tool_claim_without_evidence",
                message="tool_evidence_ref did not resolve to an artifact",
                evidence_ref="$.tool_evidence_ref",
            )
        )
        return None

    expected = _artifact_ref_digest(ref)
    actual = sha256_bytes(raw)
    if expected and actual != expected:
        failures.append(
            ToolClaimFailure(
                tag="tool_evidence_fingerprint_mismatch",
                message="tool_evidence_ref hash does not match stored artifact bytes",
                evidence_ref="$.tool_evidence_ref",
            )
        )
        return None

    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        failures.append(
            ToolClaimFailure(
                tag="tool_evidence_corrupt",
                message=f"tool evidence artifact is not valid JSON: {exc}",
                evidence_ref="$.tool_evidence_ref",
            )
        )
        return None
    if not isinstance(parsed, dict):
        failures.append(
            ToolClaimFailure(
                tag="tool_evidence_corrupt",
                message="tool evidence artifact did not decode to an object",
                evidence_ref="$.tool_evidence_ref",
            )
        )
        return None
    return parsed


def _artifact_ref_digest(ref: str) -> str | None:
    raw = str(ref or "").strip()
    if not raw.startswith("artifact:sha256:"):
        return None
    digest = raw.split("artifact:sha256:", 1)[1].strip().lower()
    if len(digest) != 64 or not all(ch in "0123456789abcdef" for ch in digest):
        return None
    return digest


def _dedupe_failures(failures: List[ToolClaimFailure]) -> List[ToolClaimFailure]:
    seen = set()
    out: List[ToolClaimFailure] = []
    for failure in failures:
        key = (failure.tag, failure.message, failure.evidence_ref or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(failure)
    return out
