# Singleton — intentionally stays in policy/. External access via ConstitutionPort only.
# State: loads constitution from disk on first call; supports reload(). Not safe to copy to shared/.
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from babyai_shared.core.constitution import (
    ConstitutionState,
    ConstitutionViolation,
    assert_artifact_ref_fingerprinted,
    assert_constitution_unchanged,
    assert_decision_has_provenance,
    assert_stagnation_terminated,
    assert_training_dataset_approved,
    assert_write_path_allowed,
    load_constitution,
)
from babyai_shared.ops.killswitch import KillSwitchViolation, get_killswitch_service


@dataclass(frozen=True)
class ConstitutionVerdict:
    allowed: bool
    action: str
    rule_id: str | None
    message: str | None
    constitution_version: str
    constitution_fingerprint: str
    effective_from: str | None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": bool(self.allowed),
            "action": str(self.action),
            "rule_id": self.rule_id,
            "message": self.message,
            "constitution_version": str(self.constitution_version),
            "constitution_fingerprint": str(self.constitution_fingerprint),
            "effective_from": self.effective_from,
        }


class ConstitutionService:
    """
    Global law-engine facade used by all write/integrity integration points.

    API:
    - validate(action, context) -> ConstitutionVerdict
    - require(action, context) -> ConstitutionVerdict or raises ConstitutionViolation
    """

    def __init__(self, *, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._state = load_constitution(path=self._path)

    @property
    def state(self) -> ConstitutionState:
        return self._state

    def metadata(self) -> Dict[str, Any]:
        return self._state.to_meta()

    def reload(self) -> ConstitutionState:
        self._state = load_constitution(path=self._path)
        return self._state

    def validate(self, action: str, context: Mapping[str, Any]) -> ConstitutionVerdict:
        normalized_action = str(action or "").strip()
        try:
            self._dispatch(normalized_action, context)
            return ConstitutionVerdict(
                allowed=True,
                action=normalized_action,
                rule_id=None,
                message=None,
                constitution_version=self._state.version,
                constitution_fingerprint=self._state.fingerprint,
                effective_from=self._state.effective_from,
            )
        except (ConstitutionViolation, KillSwitchViolation) as exc:
            return ConstitutionVerdict(
                allowed=False,
                action=normalized_action,
                rule_id=str(getattr(exc, "rule_id", "constitution_violation")),
                message=str(exc),
                constitution_version=self._state.version,
                constitution_fingerprint=self._state.fingerprint,
                effective_from=self._state.effective_from,
            )

    def require(self, action: str, context: Mapping[str, Any]) -> ConstitutionVerdict:
        verdict = self.validate(action, context)
        if not verdict.allowed:
            raise ConstitutionViolation(
                rule_id=str(verdict.rule_id or "constitution_violation"),
                message=str(verdict.message or "constitution rejected action"),
            )
        return verdict

    def _dispatch(self, action: str, context: Mapping[str, Any]) -> None:
        self._assert_effective_from()
        killswitch = get_killswitch_service()
        if action == "write_path":
            path = context.get("path")
            if path is None:
                raise ConstitutionViolation(
                    rule_id="missing_write_path",
                    message="write_path action requires context['path']",
                )
            killswitch.require_write(
                operation=str(context.get("operation") or "write_path"),
                scope=_as_optional_str(context.get("service") or context.get("write_scope")),
                context=context,
            )
            assert_write_path_allowed(
                str(path),
                constitution=self._state,
                env=_as_str_mapping(context.get("env")),
                repo_root=_as_optional_path(context.get("repo_root")),
            )
            return

        if action == "training_dataset":
            dataset_path = context.get("dataset_path")
            if dataset_path is None:
                raise ConstitutionViolation(
                    rule_id="missing_training_dataset",
                    message="training_dataset action requires context['dataset_path']",
                )
            killswitch.require_write(
                operation=str(context.get("operation") or "training_dataset"),
                scope="TRAIN_WRITE",
                context=context,
            )
            assert_training_dataset_approved(
                str(dataset_path),
                constitution=self._state,
                env=_as_str_mapping(context.get("env")),
                repo_root=_as_optional_path(context.get("repo_root")),
            )
            return

        if action == "artifact_ref":
            assert_artifact_ref_fingerprinted(
                _as_optional_str(context.get("ref")),
                field_name=str(context.get("field_name") or "artifact_ref"),
                constitution=self._state,
            )
            return

        if action == "decision_provenance":
            decision = context.get("decision")
            if decision is not None and not isinstance(decision, Mapping):
                raise ConstitutionViolation(
                    rule_id="invalid_decision_payload",
                    message="decision_provenance expects mapping decision payload",
                )
            assert_decision_has_provenance(
                decision,
                context_id=_as_optional_str(context.get("context_id")),
                constitution=self._state,
            )
            return

        if action == "stagnation_terminated":
            repair_history = context.get("repair_history")
            if not isinstance(repair_history, list):
                repair_history = []
            assert_stagnation_terminated(
                repair_history,
                stop_reason=_as_optional_str(context.get("stop_reason")),
                success=bool(context.get("success")),
                constitution=self._state,
            )
            return

        if action == "constitution_unchanged":
            assert_constitution_unchanged(
                self._state,
                path=_as_optional_path(context.get("path")) or self._path,
            )
            return

        raise ConstitutionViolation(
            rule_id="unknown_constitution_action",
            message=f"unknown constitution action: {action!r}",
        )

    def _assert_effective_from(self) -> None:
        effective_from = self._state.effective_from
        if not isinstance(effective_from, str) or not effective_from.strip():
            return
        try:
            dt = datetime.fromisoformat(effective_from.replace("Z", "+00:00"))
        except Exception as exc:
            raise ConstitutionViolation(
                rule_id="invalid_effective_from",
                message=f"invalid effective_from format: {effective_from} ({exc})",
            ) from exc
        now = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt > now:
            raise ConstitutionViolation(
                rule_id="constitution_not_effective_yet",
                message=f"constitution effective_from is in the future: {effective_from}",
            )


_SERVICE: ConstitutionService | None = None


def get_constitution_service(*, path: str | Path | None = None, reload: bool = False) -> ConstitutionService:
    global _SERVICE
    if _SERVICE is None or path is not None:
        _SERVICE = ConstitutionService(path=path)
        return _SERVICE
    if reload:
        _SERVICE.reload()
    return _SERVICE


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _as_optional_path(value: Any) -> Path | None:
    raw = _as_optional_str(value)
    return Path(raw) if raw else None


def _as_str_mapping(value: Any) -> Mapping[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    out: Dict[str, str] = {}
    for key, raw in value.items():
        k = str(key).strip()
        if not k:
            continue
        out[k] = str(raw)
    return out
