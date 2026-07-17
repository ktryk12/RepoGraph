from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import jsonschema
import yaml

_SCHEMA_PATH = Path("schemas/judge_summary/v1.json")
_POLICY_PATH = Path("policy/promotion_gate_policy.yaml")


def _load_policy(path: Path | str | None = None) -> dict:
    policy_path = Path(path) if path else _POLICY_PATH
    with policy_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _load_schema() -> jsonschema.Draft202012Validator:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    return jsonschema.Draft202012Validator(schema)


def _validate_summary(summary: dict) -> bool:
    try:
        validator = _load_schema()
        validator.validate(summary)
        return True
    except Exception:
        return False


def _extract_error_codes(summary: dict) -> Tuple[int, List[str]]:
    reasons = (summary.get("reasons") or {}).get("top") or []
    error_codes = [str(reason.get("code")) for reason in reasons if str(reason.get("severity") or "").lower() == "error"]
    error_count = (summary.get("reasons") or {}).get("by_severity", {}).get("error", 0)
    try:
        error_count = int(error_count)
    except Exception:
        error_count = len(error_codes)
    return error_count, sorted(set(code for code in error_codes if code))


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reasons: List[str]


def can_promote(
    summary: dict | None,
    *,
    mode: str = "stable",
    canary_level: int = 0,
    policy_path: Path | str | None = None,
) -> GateDecision:
    policy = _load_policy(policy_path)
    gate_policy = (policy.get("promotion_gate") or {}).get(mode) or {}

    if not summary:
        return GateDecision(False, ["PROMOTION_DENIED_SUMMARY_MISSING"])

    if not _validate_summary(summary):
        return GateDecision(False, ["PROMOTION_DENIED_SUMMARY_INVALID"])

    error_count, error_codes = _extract_error_codes(summary)

    if mode == "stable":
        max_errors = int(gate_policy.get("max_errors", 0))
        if error_count > max_errors:
            return GateDecision(False, ["PROMOTION_DENIED_STABLE_ERRORS_PRESENT"])
        return GateDecision(True, ["PROMOTION_ALLOWED_STABLE"])

    if mode == "canary":
        if canary_level <= 0:
            return GateDecision(False, ["PROMOTION_DENIED_CANARY_POLICY_VIOLATION"])
        max_errors = int(gate_policy.get("max_errors", 0))
        allowlist = gate_policy.get("allow_error_codes") or []
        if error_count > max_errors:
            return GateDecision(False, ["PROMOTION_DENIED_CANARY_TOO_MANY_ERRORS"])
        if allowlist:
            allowset = {str(code) for code in allowlist}
            if not set(error_codes).issubset(allowset):
                return GateDecision(False, ["PROMOTION_DENIED_CANARY_ERRORS_NOT_ALLOWLISTED"])
        return GateDecision(True, ["PROMOTION_ALLOWED_CANARY"])

    return GateDecision(False, ["PROMOTION_DENIED_SUMMARY_INVALID"])
