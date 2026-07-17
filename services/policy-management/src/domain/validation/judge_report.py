from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import json
import jsonschema

from policy.reason_taxonomy import load_reason_taxonomy, validate_reason_codes

_SCHEMA_PATH = Path("schemas/judge_report/v1.json")


def _validator() -> jsonschema.Draft202012Validator:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as handle:
        schema = json.load(handle)
    return jsonschema.Draft202012Validator(schema)


def build_judge_report(
    *,
    verdict: str,
    reasons: Iterable[str],
    taxonomy_path: Path | str | None = None,
    evidence_refs: Iterable[str] | None = None,
    notes: dict | None = None,
) -> dict:
    reasons_list = [str(reason) for reason in reasons if reason]
    if not reasons_list:
        raise ValueError("JudgeReport must have at least one reason code.")
    taxonomy = load_reason_taxonomy(Path(taxonomy_path) if taxonomy_path else Path("policy/reason_taxonomy.yaml"))
    unknown = validate_reason_codes(reasons_list, taxonomy)
    if unknown:
        raise ValueError(f"unknown reason codes: {unknown}")

    payload = {
        "schema_version": 1,
        "kind": "JudgeReport",
        "verdict": verdict,
        "reasons": reasons_list,
        "evidence_refs": list(evidence_refs or []),
        "notes": notes or {},
    }
    validate_judge_report(payload)
    return payload


def validate_judge_report(report: dict) -> None:
    validator = _validator()
    validator.validate(report)
