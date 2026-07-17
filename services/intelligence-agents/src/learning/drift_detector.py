from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from babyai.learning.fast_path import FastPathRegistry
from babyai.learning.pattern_agent import Pattern, _extract_samples, _load_event_rows, _parse_since, _utc_now


@dataclass(frozen=True)
class DriftStatus:
    status: str
    baseline_hit_rate: float
    observed_hit_rate: float
    sample_size: int
    checked_at: str


class DriftDetector:
    def __init__(self, memory_ref: Any, decay_window: Any) -> None:
        self.memory_ref = memory_ref
        self.decay_window = decay_window
        self._fast_path_registry: FastPathRegistry | None = None

    def attach_fast_path_registry(self, registry: FastPathRegistry) -> None:
        self._fast_path_registry = registry

    def check_pattern(self, pattern: Pattern) -> DriftStatus:
        now = _utc_now()
        expires_at = _parse_since(pattern.expires_at, now=now)
        if expires_at is not None and now >= expires_at:
            self._expire_pattern(pattern)
            return DriftStatus(
                status="expired",
                baseline_hit_rate=float(pattern.hit_rate),
                observed_hit_rate=0.0,
                sample_size=0,
                checked_at=_to_iso(now),
            )

        since = _resolve_since(now=now, decay_window=self.decay_window)
        project_id = _project_id_from_pattern(pattern)
        domain = _domain_from_pattern(pattern)
        rows = _load_event_rows(
            memory_ref=self.memory_ref,
            project_ids=[project_id],
            domain=domain,
            since=since,
        )
        samples = _extract_samples(rows)
        matched = [sample for sample in samples if _sample_matches_pattern(sample, pattern)]
        sample_size = len(matched)
        hits = len([sample for sample in matched if str(sample.get("outcome")) == str(pattern.outcome)])
        observed = 0.0 if sample_size <= 0 else float(hits) / float(sample_size)

        baseline = float(pattern.hit_rate)
        status = "stable"
        if sample_size >= 5 and observed < (baseline * 0.8):
            status = "degrading"
        return DriftStatus(
            status=status,
            baseline_hit_rate=baseline,
            observed_hit_rate=observed,
            sample_size=sample_size,
            checked_at=_to_iso(now),
        )

    def _expire_pattern(self, pattern: Pattern) -> None:
        if self._fast_path_registry is not None:
            self._fast_path_registry.remove_by_pattern(pattern)


def _resolve_since(*, now: datetime, decay_window: Any) -> datetime:
    if isinstance(decay_window, (int, float)):
        seconds = max(1.0, float(decay_window))
        return now - timedelta(seconds=seconds)
    if isinstance(decay_window, str):
        text = str(decay_window).strip().lower()
        if text.endswith("h"):
            return now - timedelta(hours=max(0.001, float(text[:-1] or 24.0)))
        if text.endswith("d"):
            return now - timedelta(days=max(0.001, float(text[:-1] or 7.0)))
    if isinstance(decay_window, dict):
        if "seconds" in decay_window:
            return now - timedelta(seconds=max(1.0, float(decay_window.get("seconds", 3600.0))))
        if "hours" in decay_window:
            return now - timedelta(hours=max(0.001, float(decay_window.get("hours", 24.0))))
        if "days" in decay_window:
            return now - timedelta(days=max(0.001, float(decay_window.get("days", 7.0))))
    return now - timedelta(days=7.0)


def _project_id_from_pattern(pattern: Pattern) -> str:
    feature = dict(pattern.feature_combo)
    clean = str(feature.get("project_id") or "").strip()
    return clean or "global"


def _domain_from_pattern(pattern: Pattern) -> str | None:
    feature = dict(pattern.feature_combo)
    clean = str(feature.get("domain") or "").strip()
    return clean or None


def _sample_matches_pattern(sample: dict[str, Any], pattern: Pattern) -> bool:
    combo = dict(pattern.feature_combo)
    current = sample.get("feature_combo")
    if not isinstance(current, dict):
        return False
    for key, expected in combo.items():
        if current.get(str(key)) != expected:
            return False
    return True


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
