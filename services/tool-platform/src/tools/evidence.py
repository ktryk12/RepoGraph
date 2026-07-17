from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Dict
import json

from tools.contracts import TOOL_EVIDENCE_PACK, TOOL_RESULT, ToolEvidencePack, ToolResult, validate_tool_contract


def write_tool_result(
    result: ToolResult,
    *,
    artifact_root: str | Path = "artifacts",
    episode_id: str = "unknown",
) -> str:
    payload = result.to_dict()
    validate_tool_contract(TOOL_RESULT, payload)

    root = Path(artifact_root)
    target = root / "tools" / str(episode_id) / str(result.tool_id) / "result.json"
    _atomic_write_json(target, payload)
    return _artifact_ref_from_bytes(_stable_json_bytes(payload))


def write_tool_evidence_pack(
    pack: ToolEvidencePack,
    *,
    artifact_root: str | Path = "artifacts",
    episode_id: str | None = None,
) -> str:
    eid = str(episode_id or pack.run_id or "unknown")

    # Persist each tool result first so layout is complete and deterministic.
    for result in pack.tool_results:
        write_tool_result(result, artifact_root=artifact_root, episode_id=eid)

    payload = pack.to_dict()
    validate_tool_contract(TOOL_EVIDENCE_PACK, payload)

    root = Path(artifact_root)
    target = root / "tools" / eid / "tool_evidence.json"
    _atomic_write_json(target, payload)
    return _artifact_ref_from_bytes(_stable_json_bytes(payload))


def _artifact_ref_from_bytes(payload: bytes) -> str:
    return f"artifact:sha256:{sha256(payload).hexdigest()}"


def _stable_json_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    from policy.constitution_service import get_constitution_service
    from verify.artifacts.registry import write_artifact
    constitution = get_constitution_service()
    artifact_type = "tool_evidence_pack_json" if path.name == "tool_evidence.json" else "tool_result_json"
    pretty = json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    write_artifact(
        artifact_type,
        pretty,
        path,
        metadata={
            "source_ref": "tools.evidence",
            "constitution_version": constitution.state.version,
        },
    )
