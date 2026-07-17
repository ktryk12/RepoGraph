from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import time


@dataclass(frozen=True)
class HotEntry:
    state_ref: str
    score: float
    ts: float


class StateTiering:
    """
    Cold artifact store + hot in-memory index.

    Cold: filesystem artifacts
    Hot: per-scope priority list (LRU tiebreak)
    """

    def __init__(
        self,
        *,
        cold_dir: Path | str = "artifacts/state",
        max_hot_per_scope: int = 3,
    ) -> None:
        self.cold_dir = Path(cold_dir)
        self.cold_dir.mkdir(parents=True, exist_ok=True)
        self.max_hot_per_scope = int(max_hot_per_scope)
        self._hot: Dict[str, List[HotEntry]] = {}

    def save_cold(self, state_blob: bytes) -> str:
        digest = sha256(state_blob).hexdigest()
        ref = f"artifact:sha256:{digest}"
        path = self._cold_path(ref)
        if not path.exists():
            path.write_bytes(state_blob)
        return ref

    def load_cold(self, state_ref: str) -> bytes:
        path = self._cold_path(state_ref)
        return path.read_bytes()

    def promote_hot(self, scope_id: str, state_ref: str, *, score: float = 0.0) -> None:
        now = time.time()
        entries = self._hot.setdefault(scope_id, [])
        entries = [e for e in entries if e.state_ref != state_ref]
        entries.append(HotEntry(state_ref=state_ref, score=float(score), ts=now))
        entries = sorted(entries, key=lambda e: (-e.score, -e.ts, e.state_ref))
        if len(entries) > self.max_hot_per_scope:
            entries = entries[: self.max_hot_per_scope]
        self._hot[scope_id] = entries

    def select_hot(self, scope_id: str, *, top_k: int = 1) -> List[str]:
        entries = self._hot.get(scope_id, [])
        return [e.state_ref for e in entries[: int(top_k)]]

    def _cold_path(self, state_ref: str) -> Path:
        digest = state_ref.split(":")[-1]
        return self.cold_dir / f"{digest}.bin"
