from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from uuid import uuid4

import json

from babyai_shared.fingerprint import canonical_json
from babyai_shared.policy.spec import validate_policy_spec


@dataclass(frozen=True)
class Suggestion:
    suggestion_id: str
    reason: str
    patch: list[Mapping[str, Any]]
    severity: str = "warning"
    auto_apply: bool = False


_DEFAULT_POLICY_VERSION = "1.0"


def _clone_spec(payload: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(canonical_json(payload))


def _parse_version(value: str) -> tuple[int, int]:
    parts = str(value).split(".")
    if len(parts) != 2:
        raise ValueError(f"invalid version format '{value}'")
    major, minor = parts
    return int(major), int(minor)


def _ensure_minor_bump(current: str, target: str) -> None:
    curr_major, curr_minor = _parse_version(current)
    target_major, target_minor = _parse_version(target)
    if target_major != curr_major or target_minor != curr_minor + 1:
        raise ValueError("policy_spec_version must be incremented by one minor version")


def _build_suggestion(reason: str, patch: list[Mapping[str, Any]]) -> Suggestion:
    return Suggestion(
        suggestion_id=uuid4().hex,
        reason=reason,
        patch=patch,
    )


def _apply_patch_op(spec: dict[str, Any], operation: Mapping[str, Any]) -> None:
    op = str(operation.get("op") or "").lower()
    path = str(operation.get("path") or "")
    if not path.startswith("/"):
        raise ValueError("patch path must be a JSON pointer starting with '/'")
    segments = path.lstrip("/").split("/")
    node: Any = spec
    for segment in segments[:-1]:
        if isinstance(node, Mapping):
            child = node.get(segment)
            if child is None:
                child = {}
                node[segment] = child
            node = child
        else:
            raise ValueError(f"invalid patch path part '{segment}'")

    last = segments[-1]
    target = node
    if op == "add":
        if last == "-":
            if not isinstance(target, list):
                raise ValueError("cannot append to non-list target")
            target.append(operation["value"])
            return
        if isinstance(target, Mapping):
            target[last] = operation["value"]
            return
        raise ValueError("invalid target for add operation")
    if op == "replace":
        if isinstance(target, Mapping):
            existing = target.get(last)
            value = operation["value"]
            if (
                isinstance(existing, (int, float))
                and isinstance(value, (int, float))
                and last == "precision"
                and "quality_profile" in segments
            ):
                if value < existing:
                    raise ValueError("cannot lower quality thresholds")
            target[last] = value
            return
        if isinstance(target, list) and last.isdigit():
            index = int(last)
            target[index] = operation["value"]
            return
        raise ValueError("invalid target for replace operation")
    raise ValueError(f"unsupported patch op '{op}'")


class PolicyEvolutionUseCase:
    def suggest(
        self,
        policy_spec: Mapping[str, Any],
        *,
        usage_summary: Mapping[str, Any] | None = None,
        failure_summary: Mapping[str, Any] | None = None,
    ) -> list[Suggestion]:
        spec = _clone_spec(policy_spec)
        validate_policy_spec(spec)
        usage = usage_summary or {}
        fail = failure_summary or {}

        suggestions: list[Suggestion] = []
        if message := fail.get("warning_gate"):
            patch = [{"op": "add", "path": "/constraints/-", "value": message}]
            suggestions.append(_build_suggestion("add warning gate constraint", patch))

        precision = usage.get("precision")
        if isinstance(precision, (int, float)) and precision < 0.92:
            current = spec.get("quality_profile", {}).get("scores", {}).get("precision")
            if isinstance(current, (int, float)):
                target = min(current + 0.04, 0.999)
                patch = [
                    {
                        "op": "replace",
                        "path": "/quality_profile/scores/precision",
                        "value": target,
                    }
                ]
                suggestions.append(_build_suggestion("raise precision threshold", patch))

        if missing := fail.get("missing_field"):
            path = missing.get("path")
            value = missing.get("value")
            if path and value is not None:
                patch = [{"op": "add", "path": f"/{path}", "value": value}]
                suggestions.append(_build_suggestion("add required field", patch))

        return suggestions

    def apply_suggestion(
        self,
        policy_spec: Mapping[str, Any],
        suggestion: Suggestion,
        *,
        approved: bool,
        policy_spec_version: str,
    ) -> dict[str, Any]:
        if not approved:
            raise ValueError("suggestions must be approved before applying")

        spec = _clone_spec(policy_spec)
        metadata = spec.setdefault("metadata", {})
        current_version = str(metadata.get("policy_spec_version") or _DEFAULT_POLICY_VERSION)
        _ensure_minor_bump(current_version, policy_spec_version)

        for operation in suggestion.patch:
            _apply_patch_op(spec, operation)

        metadata["policy_spec_version"] = policy_spec_version
        validate_policy_spec(spec)
        return spec
