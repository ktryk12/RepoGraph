from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import os

from policy.approval_gate import approval_required


class PolicyViolationError(RuntimeError):
    pass


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    profile: str
    risk_rating: str
    required_permissions: tuple[str, ...]
    requires_approval: bool
    reason: str


_DEFAULT_PROFILE_CONFIG: dict[str, dict[str, Any]] = {
    "safe": {
        "risk_rating": "low",
        "required_permissions": ["visual.generate.safe"],
        "content_label": "sfw",
    },
    "photo": {
        "risk_rating": "medium",
        "required_permissions": ["visual.generate.photo"],
        "content_label": "photo",
    },
    "artistic": {
        "risk_rating": "low",
        "required_permissions": ["visual.generate.artistic"],
        "content_label": "artistic",
    },
    "nsfw": {
        "risk_rating": "high",
        "required_permissions": ["visual.generate.nsfw"],
        "content_label": "nsfw",
        "requires_policy_flag": "allow_nsfw",
        "require_governance": True,
    },
    "video": {
        "risk_rating": "medium",
        "required_permissions": ["visual.generate.video"],
        "content_label": "video",
    },
}


class ContentPolicy:
    def __init__(
        self,
        project_id: str,
        policy_config: Mapping[str, Any] | None = None,
        config_guard_ref: Any | None = None,
    ) -> None:
        self.project_id = str(project_id or "").strip()
        if not self.project_id:
            raise ValueError("project_id must be non-empty")
        self.policy_config = dict(policy_config or {})
        if config_guard_ref is not None:
            self._config_guard_ref = config_guard_ref
        else:
            from aesa.bootstrap.config_guard import validate_startup_config
            self._config_guard_ref = validate_startup_config

    def check(self, request: Mapping[str, Any]) -> PolicyDecision:
        if not isinstance(request, Mapping):
            raise PolicyViolationError("policy request must be a mapping")

        self._validate_config_guard_or_raise()

        profile = str(request.get("style_profile") or "safe").strip().lower()
        profile_cfg = self.profile_config(profile)

        required_permissions = _to_tuple(profile_cfg.get("required_permissions"))
        risk_rating = _normalize_risk(profile_cfg.get("risk_rating"))
        requires_approval = bool(profile_cfg.get("require_governance", False))

        project_policy = request.get("project_policy")
        policy_mapping = dict(project_policy) if isinstance(project_policy, Mapping) else dict(self.policy_config)

        required_flag = str(profile_cfg.get("requires_policy_flag") or "").strip()
        if required_flag and not bool(policy_mapping.get(required_flag, False)):
            raise PolicyViolationError(f"policy block: '{required_flag}' must be true for profile={profile}")

        if requires_approval:
            self._require_governance_permit_or_raise(
                request=request,
                policy_mapping=policy_mapping,
            )

        return PolicyDecision(
            allowed=True,
            profile=profile,
            risk_rating=risk_rating,
            required_permissions=required_permissions,
            requires_approval=requires_approval,
            reason="policy_passed",
        )

    def profile_config(self, profile: str) -> dict[str, Any]:
        clean_profile = str(profile or "").strip().lower()
        profiles = self._profiles()
        cfg = profiles.get(clean_profile)
        if not isinstance(cfg, Mapping):
            known = ", ".join(sorted(profiles.keys()))
            raise PolicyViolationError(f"unsupported style_profile: {clean_profile} (known: {known})")
        return dict(cfg)

    def _profiles(self) -> dict[str, dict[str, Any]]:
        configured = self.policy_config.get("profiles")
        out: dict[str, dict[str, Any]] = {}
        if isinstance(configured, Mapping):
            for raw_key, raw_value in configured.items():
                key = str(raw_key or "").strip().lower()
                if not key:
                    continue
                if isinstance(raw_value, Mapping):
                    out[key] = dict(raw_value)
        for key, value in _DEFAULT_PROFILE_CONFIG.items():
            out.setdefault(key, dict(value))
        return out

    def _validate_config_guard_or_raise(self) -> None:
        guard = self._config_guard_ref
        entrypoint = str(self.policy_config.get("entrypoint") or "image_service").strip() or "image_service"
        env_payload = self.policy_config.get("env")
        source_env: Mapping[str, str]
        if isinstance(env_payload, Mapping):
            source_env = {str(k): str(v) for k, v in env_payload.items()}
        else:
            source_env = os.environ

        try:
            if callable(guard):
                guard(env=source_env, entrypoint=entrypoint)
                return
            validate_method = getattr(guard, "validate_startup_config", None)
            if callable(validate_method):
                validate_method(env=source_env, entrypoint=entrypoint)
                return
            raise PolicyViolationError("config_guard_ref is not callable")
        except Exception as exc:
            from aesa.bootstrap.config_guard import ConfigGuardError
            if isinstance(exc, ConfigGuardError):
                raise PolicyViolationError(f"config_guard_blocked: {exc}") from exc
            raise

    def _require_governance_permit_or_raise(
        self,
        *,
        request: Mapping[str, Any],
        policy_mapping: Mapping[str, Any],
    ) -> None:
        effective_policy = policy_mapping.get("effective_policy")
        constraints = policy_mapping.get("policy_constraints")
        policy_preset = str(policy_mapping.get("policy_preset") or "")
        needs_permit = approval_required(
            effective_policy=effective_policy if isinstance(effective_policy, Mapping) else None,
            policy_constraints=constraints if isinstance(constraints, Mapping) else None,
            policy_preset=policy_preset,
            required_safety_profiles=("nsfw",),
        )
        if not needs_permit:
            # NSFW generation always travels through governance permit path.
            needs_permit = True

        if not needs_permit:
            return

        permit_payload = (
            request.get("execution_permit")
            or request.get("approval_token")
            or policy_mapping.get("execution_permit")
            or policy_mapping.get("approval_token")
        )
        decision_id = str(request.get("decision_id") or policy_mapping.get("decision_id") or "").strip()
        policy_fingerprint = str(
            request.get("policy_fingerprint") or policy_mapping.get("policy_fingerprint") or ""
        ).strip()
        try:
            from aesa.domain.approval import require_execution_permit_from_mapping
            require_execution_permit_from_mapping(
                permit_payload,
                decision_id=decision_id,
                policy_fingerprint=policy_fingerprint,
            )
        except ValueError as exc:
            raise PolicyViolationError(f"policy block: governance permit missing ({exc})") from exc


def _to_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return tuple()
    out = [str(item).strip() for item in value if str(item).strip()]
    return tuple(out)


def _normalize_risk(value: Any) -> str:
    clean = str(value or "medium").strip().lower()
    if clean in {"low", "medium", "high"}:
        return clean
    return "medium"
