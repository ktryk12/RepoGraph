from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List, Optional

from policy.judge_report import build_judge_report
from policy.reason_taxonomy import load_reason_taxonomy
from verify.artifacts.writer import write_artifact

REASON_PASS = "DETREVIEW_PASS"
REASON_MANIFEST_BYPASS = "DETREVIEW_MANIFEST_WRITE_BYPASS"
REASON_HOMEBREW_HASH = "DETREVIEW_HOMEBREW_HASH"
REASON_MISSING_FIELDS = "DETREVIEW_MISSING_CONTRACT_FIELDS"
REASON_GATE_DENIED = "DETREVIEW_GATE_DENIED"
REASON_UNKNOWN_CODE = "DETREVIEW_UNKNOWN_REASON_CODE"

ALLOWED_MANIFEST_WRITERS = {
    Path("verify/artifacts/writer.py"),
    Path("verify/artifacts/registry.py"),
}

CHECK_A_DIRS = ["scripts", "verify", "aesa/api"]
CHECK_B_DIRS = ["verify/artifacts", "aesa/api"]
CHECK_B_FILES = [Path("scripts/promotion_utils.py")]

# tokens for matching
PATTERN_MANIFEST = ["manifest.jsonl", "artifacts/registry/"]
PATTERN_WRITE = ["write_text", "write_bytes", "open(", "json.dump", ".write("]
PATTERN_HASH = ["hashlib.sha256", "json.dumps("]


def _scan_files(directories: Iterable[str]) -> Iterable[Path]:
    for directory in directories:
        base = Path(directory)
        if not base.exists():
            continue
        yield from sorted(base.rglob("*.py"))


def _check_a(directories: Iterable[str]) -> List[str]:
    errors = []
    for path in _scan_files(directories):
        if path in ALLOWED_MANIFEST_WRITERS:
            continue
        if path.parts and path.parts[0].startswith("tests"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if any(token in line for token in PATTERN_MANIFEST) and any(token in line for token in PATTERN_WRITE):
                errors.append(f"{path}: write to artifact registry detected")
                break
    return errors


def _check_b(directories: Iterable[str]) -> List[str]:
    errors = []
    files = list(_scan_files(directories))
    files.extend(p for p in CHECK_B_FILES if p.exists())
    for path in files:
        if path in ALLOWED_MANIFEST_WRITERS:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for line in text.splitlines():
            if any(token in line for token in PATTERN_HASH) and any(write_token in line for write_token in PATTERN_WRITE):
                errors.append(f"{path}: direct hashing/serialization found")
                break
    return errors


def _check_contract(reason_taxonomy: dict, writer_manifest: Path, produced_codes: List[str]) -> List[str]:
    errors: List[str] = []
    writer_manifest.parent.mkdir(parents=True, exist_ok=True)
    good_payload = {"kind": "JudgeReport", "schema_version": 1, "verdict": "PASS", "reasons": [REASON_PASS], "evidence_refs": []}
    bad_payload = {"verdict": "PASS"}
    try:
        write_artifact(
            artifact_type="judge_report",
            payload=good_payload,
            output_path=writer_manifest.parent / "judge_report.json",
            registry_manifest=writer_manifest,
            required_fields=("kind", "schema_version"),
        )
    except Exception as exc:  # pragma: no cover - should not happen
        errors.append(f"{REASON_GATE_DENIED} good contract write failed: {exc}")
    try:
        write_artifact(
            artifact_type="judge_report",
            payload=bad_payload,
            output_path=writer_manifest.parent / "judge_report_invalid.json",
            registry_manifest=writer_manifest,
            required_fields=("kind", "schema_version"),
        )
    except ValueError:
        return errors
    except Exception as exc:
        errors.append(f"{REASON_GATE_DENIED} unexpected failure: {exc}")
        return errors
    errors.append(REASON_MISSING_FIELDS)
    return errors


def _load_manifest(manifest_path: Path) -> List[str]:
    if not manifest_path.exists():
        return []
    return manifest_path.read_text(encoding="utf-8").splitlines()


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic review runner for artifacts")
    parser.add_argument("--output", default="artifacts/deterministic_review/judgereport.json")
    parser.add_argument("--registry-manifest", default="artifacts/registry/manifest.jsonl")
    parser.add_argument("--script-dir", default="scripts")
    parser.add_argument("--verify-dir", default="verify")
    parser.add_argument("--aesa-api-dir", default="aesa/api")
    parser.add_argument("--taxonomy", default="policy/reason_taxonomy.yaml")
    parser.add_argument("--check-b-dir", action="append", default=None)
    args = parser.parse_args(list(argv) if argv else None)

    messages: List[str] = []
    reason_codes: List[str] = []
    taxonomy = load_reason_taxonomy(Path(args.taxonomy))
    errors_a = _check_a([args.script_dir, args.verify_dir, args.aesa_api_dir])
    if errors_a:
        messages.extend(errors_a)
        reason_codes.append(REASON_MANIFEST_BYPASS)
    b_dirs = args.check_b_dir or CHECK_B_DIRS
    errors_b = _check_b(b_dirs)
    if errors_b:
        messages.extend(errors_b)
        reason_codes.append(REASON_HOMEBREW_HASH)
    registry_manifest = Path(args.registry_manifest)
    contract_codes: List[str] = []
    contract_errors = _check_contract(taxonomy, registry_manifest, contract_codes)
    messages.extend(contract_errors)
    reason_codes.extend(contract_codes)

    if not messages:
        reason_codes.append(REASON_PASS)
    verdict = "PASS" if not messages else "FAIL"
    notes = {
        "script_dir": args.script_dir,
        "verify_dir": args.verify_dir,
        "aesa_api_dir": args.aesa_api_dir,
    }
    try:
        report = build_judge_report(
            verdict=verdict,
            reasons=reason_codes or [REASON_PASS],
            taxonomy_path=Path(args.taxonomy),
            notes=notes,
        )
    except ValueError as exc:
        messages.append(str(exc))
        verdict = "FAIL"
        report = build_judge_report(
            verdict=verdict,
            reasons=[REASON_UNKNOWN_CODE],
            taxonomy_path=Path(args.taxonomy),
            notes=notes,
        )
    report["notes"].update({"unknown_messages": messages})
    write_artifact(
        artifact_type="judge_report",
        payload=report,
        output_path=Path(args.output),
        registry_manifest=registry_manifest,
        required_fields=("kind", "schema_version"),
    )

    return 0 if not messages else 1


if __name__ == "__main__":
    raise SystemExit(main())
