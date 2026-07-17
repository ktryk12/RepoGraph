# Constitution Governance

## Scope
This governance applies to:
- `policy/constitution.yaml`
- `policy/constitution_service.py`
- `policy/constitution_validator_cli.py`

## Ownership
- Constitution files are protected by CODEOWNERS.
- Minimum review: at least one CODEOWNER approval.

## Change Process
1. Open PR with a dedicated **Constitution Impact** section.
2. Run `python -m policy.constitution_validator_cli --current policy/constitution.yaml`.
3. If constitution behavior changes, add PR label: `breaking change`.
4. CI enforces:
- writer-gate test (`tests/architecture/test_constitution_writer_gate.py`)
- PR label gate when constitution files change.

## Versioning
- `version`: semantic policy version (e.g. `v1`, `v1.1`).
- `effective_from`: UTC ISO-8601 activation time (`YYYY-MM-DDTHH:MM:SSZ`).
- `updated_at`: UTC ISO-8601 authoring time.

## Breaking Change Criteria
Treat as breaking if any of the following change:
- a rule is removed or renamed
- an existing rule gets stricter behavior
- required writer actions change (`write_path`, `training_dataset`, etc.)

## Required Constitutional Rules (v1)
- `no_unapproved_training_data`
- `decision_requires_provenance`
- `no_self_modification_without_human_approval`
- `artifact_fingerprint_required`
- `stagnation_must_terminate`
