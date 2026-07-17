from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from babyai.learning.pattern_agent import Pattern


@dataclass(frozen=True)
class FastPath:
    id: str
    pattern: Pattern
    decision_template: dict[str, Any]
    usage_count: int
    last_used: str | None


class FastPathRegistry:
    def __init__(self, confidence_threshold: float = 0.85) -> None:
        self.confidence_threshold = max(0.0, min(1.0, float(confidence_threshold)))
        self._lock = RLock()
        self._rows: dict[str, FastPath] = {}

    def register(self, pattern: Pattern, decision_template: Any) -> str:
        if float(pattern.confidence) < float(self.confidence_threshold):
            raise ValueError("no FastPath with confidence below threshold is allowed")
        template = decision_template if isinstance(decision_template, dict) else {"value": decision_template}
        fast_path_id = str(uuid4())
        row = FastPath(
            id=fast_path_id,
            pattern=pattern,
            decision_template=dict(template),
            usage_count=0,
            last_used=None,
        )
        with self._lock:
            self._rows[fast_path_id] = row
        return fast_path_id

    def lookup(self, context: Any) -> FastPath | None:
        clean_context = _normalize_context(context)
        with self._lock:
            candidates = [row for row in self._rows.values() if _matches_context(row.pattern, clean_context)]
            if not candidates:
                return None
            candidates.sort(key=lambda item: (-float(item.pattern.confidence), -int(item.pattern.sample_size), item.id))
            selected = candidates[0]
            updated = replace(
                selected,
                usage_count=int(selected.usage_count) + 1,
                last_used=_utc_now_iso(),
            )
            self._rows[selected.id] = updated
            return updated

    def remove(self, fast_path_id: str) -> None:
        clean_id = str(fast_path_id or "").strip()
        if not clean_id:
            raise ValueError("fast_path_id must be non-empty")
        with self._lock:
            self._rows.pop(clean_id, None)

    def remove_by_pattern(self, pattern: Pattern) -> int:
        removed = 0
        with self._lock:
            doomed = [row_id for row_id, row in self._rows.items() if row.pattern == pattern]
            for row_id in doomed:
                self._rows.pop(row_id, None)
                removed += 1
        return removed

    def all(self) -> list[FastPath]:
        with self._lock:
            return list(self._rows.values())


def _normalize_context(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        if isinstance(value.get("feature_combo"), dict):
            return dict(value["feature_combo"])
        return dict(value)
    return {}


def _matches_context(pattern: Pattern, context: dict[str, Any]) -> bool:
    combo = dict(pattern.feature_combo)
    for key, expected in combo.items():
        actual = context.get(str(key))
        if actual != expected:
            return False
    outcome = context.get("outcome")
    if outcome is not None and str(outcome) != str(pattern.outcome):
        return False
    return True


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
