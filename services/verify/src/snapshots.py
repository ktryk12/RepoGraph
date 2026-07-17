from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import os


_UPDATE = os.getenv("UPDATE_SNAPSHOTS", "").lower() in {"1", "true", "yes"}


def assert_json_snapshot(actual: Any, path: str | Path) -> None:
    path = Path(path)
    if _UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        return

    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected


def assert_text_snapshot(actual: str, path: str | Path) -> None:
    path = Path(path)
    if _UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual.rstrip() + "\n", encoding="utf-8")
        return

    expected = path.read_text(encoding="utf-8").strip()
    assert actual == expected
