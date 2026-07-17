from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Dict, List, Optional


STANDARD_ARTIFACT_TYPES = ["plan", "patch", "tests", "review", "ops", "docs"]


@dataclass(frozen=True)
class HandoffArtifact:
    artifact_type: str
    artifact_ref: str
    summary: str | None = None

    def to_dict(self) -> Dict[str, str]:
        data = {
            "artifact_type": self.artifact_type,
            "artifact_ref": self.artifact_ref,
        }
        if self.summary:
            data["summary"] = self.summary
        return data


def build_pr_bundle(
    *,
    scope_id: str,
    decision_id: str,
    artifacts: Dict[str, HandoffArtifact],
    created_at: Optional[str] = None,
) -> Dict[str, object]:
    """
    Build a deterministic PR bundle from handoff artifacts.
    """
    for key in STANDARD_ARTIFACT_TYPES:
        if key not in artifacts:
            raise ValueError(f"Missing handoff artifact: {key}")

    created = created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    bundle_id = _bundle_id(scope_id, decision_id, artifacts)

    return {
        "schema_version": 1,
        "bundle_id": bundle_id,
        "scope_id": scope_id,
        "decision_id": decision_id,
        "created_at": created,
        "artifacts": {k: artifacts[k].to_dict() for k in STANDARD_ARTIFACT_TYPES},
    }


def _bundle_id(scope_id: str, decision_id: str, artifacts: Dict[str, HandoffArtifact]) -> str:
    parts = [scope_id, decision_id]
    for key in sorted(artifacts.keys()):
        parts.append(f"{key}:{artifacts[key].artifact_ref}")
    payload = "|".join(parts).encode("utf-8")
    return f"bundle:sha256:{sha256(payload).hexdigest()}"
