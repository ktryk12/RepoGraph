from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import json
import yaml

from babyai_shared.fingerprint import canonical_json
from babyai_shared.policy.spec import validate_policy_spec

_DEFAULT_TEMPLATES = Path(__file__).resolve().parents[1] / "docs" / "policies" / "templates.yaml"
_CUSTOMIZATION_WHITELIST: frozenset[str] = frozenset({"goal", "output.format", "write_scope.targets"})


def _clone_spec(payload: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(canonical_json(payload))


@dataclass(frozen=True)
class PolicyTemplate:
    template_id: str
    name: str
    description: str
    policy_spec_base: dict[str, Any]


def _load_templates(path: Path) -> dict[str, PolicyTemplate]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    entries = raw.get("templates") if isinstance(raw, dict) else None
    if not isinstance(entries, Sequence):
        raise ValueError(f"invalid template file structure at {path.as_posix()}")

    templates: dict[str, PolicyTemplate] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        template_id = str(entry.get("id") or "").strip()
        if not template_id:
            continue
        spec = entry.get("policy_spec_base")
        if not isinstance(spec, Mapping):
            raise ValueError(f"policy_spec_base must be defined for template {template_id}")

        spec_copy = _clone_spec(spec)
        validate_policy_spec(spec_copy)

        templates[template_id] = PolicyTemplate(
            template_id=template_id,
            name=str(entry.get("name") or template_id),
            description=str(entry.get("description") or ""),
            policy_spec_base=spec_copy,
        )

    if not templates:
        raise ValueError("no policy templates loaded")
    return templates


class TemplateRegistry:
    def __init__(self, *, templates_file: Path | None = None):
        path = templates_file or _DEFAULT_TEMPLATES
        self._templates = _load_templates(path)

    def list_templates(self) -> list[PolicyTemplate]:
        return list(self._templates.values())

    def load_template(self, template_id: str) -> dict[str, Any]:
        template = self._templates.get(template_id)
        if template is None:
            raise KeyError(f"template {template_id} not found")
        return _clone_spec(template.policy_spec_base)

    def apply_customizations(self, template_id: str, customizations: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(customizations, Mapping):
            raise ValueError("customizations must be a mapping")
        for key in customizations.keys():
            if key not in _CUSTOMIZATION_WHITELIST:
                raise ValueError(f"customization '{key}' is not allowed")

        spec = self.load_template(template_id)
        for path, value in customizations.items():
            self._set_customization(spec, path, value)

        validate_policy_spec(spec)
        return spec

    def _set_customization(self, spec: dict[str, Any], path: str, value: Any) -> None:
        if "." in path:
            segments = path.split(".")
            node: dict[str, Any] = spec
            for part in segments[:-1]:
                child = node.get(part)
                if not isinstance(child, dict):
                    raise ValueError(f"cannot customize path '{path}'")
                node = child
            node[segments[-1]] = value
        else:
            spec[path] = value
