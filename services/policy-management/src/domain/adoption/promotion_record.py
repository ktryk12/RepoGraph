from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List

import jsonschema
import yaml

from babyai_shared.fingerprint import canonical_json_bytes
from babyai_shared.fingerprint import sha256_bytes

_SCHEMA_PATH = Path("schemas/promotion_record/v1.json")


def _load_schema() -> jsonschema.Draft202012Validator:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    return jsonschema.Draft202012Validator(schema)


def compute_policy_hash(path: Path | str) -> str:
    policy_path = Path(path)
    with policy_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    return sha256_bytes(canonical_json_bytes(data))


def build_promotion_record(
    *,
    family: str,
    mode: str,
    canary_level: int,
    promoted_model_fingerprint: str,
    judge_summary_fingerprint: str,
    gate_reasons: Iterable[str],
    policy_hash: str,
    evaluation_refs: Iterable[str] | None = None,
    notes: dict | None = None,
) -> dict:
    payload = {
        "schema_version": 1,
        "kind": "PromotionRecord",
        "family": family,
        "mode": mode,
        "canary_level": canary_level,
        "promoted_model_fingerprint": promoted_model_fingerprint,
        "judge_summary_fingerprint": judge_summary_fingerprint,
        "gate_reasons": [str(reason) for reason in gate_reasons if reason],
        "policy_hash": policy_hash,
        "evaluation_refs": list(evaluation_refs or []),
        "notes": notes or {},
    }
    validate_promotion_record(payload)
    return payload


def validate_promotion_record(record: dict) -> None:
    validator = _load_schema()
    validator.validate(record)
