from __future__ import annotations

import json
from typing import Any, Mapping

from planner.application.ports import TaskSpecStore
from babyai_shared.storage.artifact_store import FileArtifactStore


class FileTaskSpecStore(TaskSpecStore):
    def __init__(self, *, artifact_root: str = "artifacts") -> None:
        self._store = FileArtifactStore(root=artifact_root)

    def store(self, *, task_spec: Mapping[str, Any]) -> str:
        body = json.dumps(dict(task_spec or {}), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        artifact = self._store.put(
            body,
            context_id=str(task_spec.get("context_id") or "dev"),
            name=str(task_spec.get("task_id") or "task:auto"),
            metadata={"type": "task_spec", "template": str(task_spec.get("template") or "auto")},
        )
        return artifact.ref
