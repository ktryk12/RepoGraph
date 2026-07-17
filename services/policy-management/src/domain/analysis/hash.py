from __future__ import annotations

import hashlib
import json
from typing import Any


def hash_payload(payload: Any) -> str:
    """
    Deterministic hash for policy inputs.
    """
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
