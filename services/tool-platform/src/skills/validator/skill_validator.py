"""
skill_runtime/validator/skill_validator.py — Schema-validering af SkillManifest.

Validerer mod skill_schema.json (JSON Schema 2020-12).
Kaster SkillValidationError med præcis fejlbeskrivelse.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import List, Optional

from skill_runtime.loader.skill_loader import SkillManifest

_log = logging.getLogger(__name__)
_SCHEMA_PATH = Path(__file__).parent / "skill_schema.json"


class SkillValidationError(ValueError):
    def __init__(self, skill_id: str, errors: List[str]) -> None:
        self.skill_id = skill_id
        self.errors   = errors
        super().__init__(f"skill_id={skill_id}: {'; '.join(errors)}")


def _load_schema() -> dict:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_manifest(manifest: SkillManifest) -> List[str]:
    errors: List[str] = []
    try:
        import jsonschema
        schema = _load_schema()
        validator = jsonschema.Draft202012Validator(schema)
        for err in validator.iter_errors(manifest.raw_frontmatter):
            errors.append(f"{'.'.join(str(p) for p in err.absolute_path) or 'root'}: {err.message}")
    except ImportError:
        # Fallback: manuel validering af required fields
        required = ["skill_id", "name", "version", "description", "domains", "expert_routing"]
        for field in required:
            if not manifest.raw_frontmatter.get(field):
                errors.append(f"{field}: required field missing")
        er = manifest.expert_routing
        allowed_models = {"code-codestral", "general", "danish", "vision"}
        if er.get("model") not in allowed_models:
            errors.append(f"expert_routing.model: must be one of {allowed_models}, got '{er.get('model')}'")
    return errors


def validate_uniqueness(manifests: List[SkillManifest]) -> List[str]:
    seen:   dict = {}
    errors: List[str] = []
    for m in manifests:
        if m.skill_id in seen:
            errors.append(
                f"duplicate skill_id='{m.skill_id}' found at {m.source_path} "
                f"and {seen[m.skill_id].source_path}"
            )
        seen[m.skill_id] = m
    return errors


def validate_all(manifests: List[SkillManifest], raise_on_error: bool = False) -> List[SkillManifest]:
    valid = []
    for m in manifests:
        errs = validate_manifest(m)
        if errs:
            _log.warning("skill_validator_invalid skill_id=%s errors=%s", m.skill_id, errs)
            if raise_on_error:
                raise SkillValidationError(m.skill_id, errs)
        else:
            valid.append(m)
    dup_errors = validate_uniqueness(valid)
    for e in dup_errors:
        _log.warning("skill_validator_duplicate %s", e)
    return valid
