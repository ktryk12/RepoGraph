from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Pattern, Sequence
import re

import yaml


DEFAULT_REASON_TAXONOMY_PATH = Path(__file__).with_name("reason_taxonomy.yaml")
REQUIRED_NAMESPACES = ("ARCH", "SEC", "DATA", "RIGHTS", "OPS", "RAG", "POLICY")


class ReasonTaxonomyViolation(ValueError):
    def __init__(self, *, rule_id: str, message: str) -> None:
        self.rule_id = str(rule_id)
        super().__init__(str(message))


@dataclass(frozen=True)
class ReasonTaxonomyState:
    path: Path
    schema_version: int
    version: str
    max_reason_codes_per_pack: int
    registered_codes: frozenset[str]
    legacy_aliases: Dict[str, str]
    dynamic_patterns: tuple[str, ...]


@dataclass(frozen=True)
class ReasonTaxonomyVerdict:
    allowed: bool
    pack_name: str
    reason_codes: List[str]
    canonical_codes: List[str]
    unknown_codes: List[str]
    max_reason_codes_per_pack: int
    rule_id: str | None = None
    message: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": bool(self.allowed),
            "pack_name": str(self.pack_name),
            "reason_codes": list(self.reason_codes),
            "canonical_codes": list(self.canonical_codes),
            "unknown_codes": list(self.unknown_codes),
            "max_reason_codes_per_pack": int(self.max_reason_codes_per_pack),
            "rule_id": self.rule_id,
            "message": self.message,
        }


class ReasonTaxonomyService:
    def __init__(self, *, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else DEFAULT_REASON_TAXONOMY_PATH
        self._state = load_reason_taxonomy(self._path)
        self._compiled_patterns = _compile_patterns(self._state.dynamic_patterns)

    @property
    def state(self) -> ReasonTaxonomyState:
        return self._state

    def reload(self) -> ReasonTaxonomyState:
        self._state = load_reason_taxonomy(self._path)
        self._compiled_patterns = _compile_patterns(self._state.dynamic_patterns)
        return self._state

    def canonicalize(self, code: str) -> str:
        raw = _normalize_code(code)
        if not raw:
            return raw
        return str(self._state.legacy_aliases.get(raw, raw))

    def validate(
        self,
        codes: Iterable[Any],
        *,
        pack_name: str,
    ) -> ReasonTaxonomyVerdict:
        normalized = _normalize_codes(codes)
        if len(normalized) > int(self._state.max_reason_codes_per_pack):
            return ReasonTaxonomyVerdict(
                allowed=False,
                pack_name=str(pack_name),
                reason_codes=normalized,
                canonical_codes=[],
                unknown_codes=[],
                max_reason_codes_per_pack=int(self._state.max_reason_codes_per_pack),
                rule_id="reason_codes_limit_exceeded",
                message=(
                    f"{pack_name}: max {self._state.max_reason_codes_per_pack} reason codes allowed per pack, "
                    f"got {len(normalized)}"
                ),
            )

        canonical: List[str] = []
        unknown: List[str] = []
        for raw in normalized:
            canon = self.canonicalize(raw)
            if self._is_registered(raw=raw, canonical=canon):
                canonical.append(canon)
            else:
                unknown.append(raw)

        if unknown:
            return ReasonTaxonomyVerdict(
                allowed=False,
                pack_name=str(pack_name),
                reason_codes=normalized,
                canonical_codes=canonical,
                unknown_codes=unknown,
                max_reason_codes_per_pack=int(self._state.max_reason_codes_per_pack),
                rule_id="unknown_reason_codes",
                message=f"{pack_name}: unknown reason codes: {unknown}",
            )

        return ReasonTaxonomyVerdict(
            allowed=True,
            pack_name=str(pack_name),
            reason_codes=normalized,
            canonical_codes=canonical,
            unknown_codes=[],
            max_reason_codes_per_pack=int(self._state.max_reason_codes_per_pack),
        )

    def require(self, codes: Iterable[Any], *, pack_name: str) -> ReasonTaxonomyVerdict:
        verdict = self.validate(codes, pack_name=pack_name)
        if verdict.allowed:
            return verdict
        raise ReasonTaxonomyViolation(
            rule_id=str(verdict.rule_id or "reason_taxonomy_violation"),
            message=str(verdict.message or f"{pack_name}: reason taxonomy validation failed"),
        )

    def _is_registered(self, *, raw: str, canonical: str) -> bool:
        if raw in self._state.registered_codes or canonical in self._state.registered_codes:
            return True
        for pattern in self._compiled_patterns:
            if pattern.fullmatch(raw) or pattern.fullmatch(canonical):
                return True
        return False


_REASON_TAXONOMY_SERVICE: ReasonTaxonomyService | None = None


def get_reason_taxonomy_service(
    *,
    path: str | Path | None = None,
    reload: bool = False,
) -> ReasonTaxonomyService:
    global _REASON_TAXONOMY_SERVICE
    if _REASON_TAXONOMY_SERVICE is None or path is not None:
        _REASON_TAXONOMY_SERVICE = ReasonTaxonomyService(path=path)
        return _REASON_TAXONOMY_SERVICE
    if reload:
        _REASON_TAXONOMY_SERVICE.reload()
    return _REASON_TAXONOMY_SERVICE


def load_reason_taxonomy(path: str | Path) -> ReasonTaxonomyState:
    target = Path(path).resolve()
    if not target.exists():
        raise ReasonTaxonomyViolation(
            rule_id="reason_taxonomy_missing",
            message=f"reason taxonomy file not found: {target.as_posix()}",
        )
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ReasonTaxonomyViolation(
            rule_id="reason_taxonomy_invalid_format",
            message=f"reason taxonomy must be mapping: {target.as_posix()}",
        )

    namespaces = payload.get("namespaces")
    if not isinstance(namespaces, Mapping):
        raise ReasonTaxonomyViolation(
            rule_id="reason_taxonomy_missing_namespaces",
            message="reason taxonomy requires top-level 'namespaces' mapping",
        )
    missing = [name for name in REQUIRED_NAMESPACES if name not in namespaces]
    if missing:
        raise ReasonTaxonomyViolation(
            rule_id="reason_taxonomy_missing_required_namespace",
            message=f"reason taxonomy missing required namespaces: {missing}",
        )

    codes: set[str] = set()
    for namespace, block in namespaces.items():
        ns = str(namespace).strip().upper()
        if not isinstance(block, Mapping):
            continue
        raw_codes = block.get("codes", [])
        if not isinstance(raw_codes, list):
            continue
        for raw in raw_codes:
            code = _normalize_code(raw)
            if not code:
                continue
            if "." not in code:
                raise ReasonTaxonomyViolation(
                    rule_id="reason_taxonomy_code_must_be_namespaced",
                    message=f"reason code '{code}' must be namespaced",
                )
            if not code.startswith(f"{ns}."):
                raise ReasonTaxonomyViolation(
                    rule_id="reason_taxonomy_namespace_mismatch",
                    message=f"reason code '{code}' does not match namespace '{ns}'",
                )
            codes.add(code)

    aliases_raw = payload.get("legacy_aliases", {})
    aliases: Dict[str, str] = {}
    if isinstance(aliases_raw, Mapping):
        for raw_key, raw_value in aliases_raw.items():
            key = _normalize_code(raw_key)
            value = _normalize_code(raw_value)
            if not key or not value:
                continue
            if value not in codes:
                raise ReasonTaxonomyViolation(
                    rule_id="reason_taxonomy_alias_target_unknown",
                    message=f"legacy alias target '{value}' is not a registered namespaced reason code",
                )
            aliases[key] = value

    patterns_raw = payload.get("dynamic_patterns", [])
    patterns: List[str] = []
    if isinstance(patterns_raw, list):
        for raw in patterns_raw:
            text = str(raw).strip()
            if not text:
                continue
            try:
                re.compile(text)
            except re.error as exc:
                raise ReasonTaxonomyViolation(
                    rule_id="reason_taxonomy_invalid_pattern",
                    message=f"invalid dynamic pattern '{text}': {exc}",
                ) from exc
            patterns.append(text)

    schema_version = _safe_int(payload.get("schema_version"), default=1)
    max_reason_codes = max(1, _safe_int(payload.get("max_reason_codes_per_pack"), default=10))
    version = str(payload.get("version") or "unknown")
    return ReasonTaxonomyState(
        path=target,
        schema_version=schema_version,
        version=version,
        max_reason_codes_per_pack=max_reason_codes,
        registered_codes=frozenset(sorted(codes)),
        legacy_aliases=dict(aliases),
        dynamic_patterns=tuple(patterns),
    )


def _compile_patterns(patterns: Sequence[str]) -> List[Pattern[str]]:
    compiled: List[Pattern[str]] = []
    for text in patterns:
        compiled.append(re.compile(str(text)))
    return compiled


def _normalize_code(value: Any) -> str:
    return str(value or "").strip()


def _normalize_codes(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for raw in values:
        code = _normalize_code(raw)
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)
