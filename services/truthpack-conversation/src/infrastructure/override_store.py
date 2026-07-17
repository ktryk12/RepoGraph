from __future__ import annotations

from pathlib import Path

from truthpack_conversation.application.ports import OverrideStore


class FileOverrideStore(OverrideStore):
    def __init__(self, *, root: str = "artifacts/truth_overrides") -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def write_override(self, *, override_hash: str, override_yaml: str) -> str:
        filename = f"{str(override_hash).strip().lower()}.yaml"
        path = self._root / filename
        payload = str(override_yaml)
        if not path.exists():
            path.write_text(payload, encoding="utf-8")
        return path.as_posix()
