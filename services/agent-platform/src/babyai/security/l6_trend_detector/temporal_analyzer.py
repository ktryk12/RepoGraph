from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List

import numpy as np

from babyai.security.event_store import SecurityEvent


@dataclass(frozen=True)
class TemporalPattern:
    kind: str
    severity: float
    details: Dict[str, float | int | str] = field(default_factory=dict)


class TemporalAnalyzer:
    def analyze(self, events: List[SecurityEvent]) -> List[TemporalPattern]:
        rows = sorted(list(events), key=lambda item: _to_utc(item.timestamp))
        patterns: List[TemporalPattern] = []
        freq = self._detect_frequency_increase(rows)
        if freq is not None:
            patterns.append(freq)
        periodicity = self._detect_periodicity(rows)
        if periodicity is not None:
            patterns.append(periodicity)
        burst = self._detect_burst_windows(rows)
        if burst is not None:
            patterns.append(burst)
        return patterns

    def _detect_frequency_increase(self, events: List[SecurityEvent]) -> TemporalPattern | None:
        if len(events) < 6:
            return None
        bins = _bin_counts(events, bucket_minutes=60)
        if len(bins) < 4:
            return None
        y = np.array([float(value) for _, value in bins], dtype=np.float64)
        x = np.arange(len(y), dtype=np.float64)
        slope, _ = np.polyfit(x, y, 1)
        baseline = float(np.mean(y)) + 1e-9
        severity = float(np.clip(max(0.0, slope) / baseline, 0.0, 1.0))
        if slope <= 0.0 or severity <= 0.0:
            return None
        return TemporalPattern(
            kind="frequency_increase",
            severity=severity,
            details={"slope": float(slope), "baseline": float(np.mean(y)), "points": int(len(y))},
        )

    def _detect_periodicity(self, events: List[SecurityEvent]) -> TemporalPattern | None:
        if len(events) < 16:
            return None
        bins = _bin_counts(events, bucket_minutes=15)
        if len(bins) < 16:
            return None
        y = np.array([float(value) for _, value in bins], dtype=np.float64)
        centered = y - np.mean(y)
        spectrum = np.abs(np.fft.rfft(centered))
        if spectrum.size <= 1:
            return None
        spectrum[0] = 0.0
        dominant_idx = int(np.argmax(spectrum))
        dominant = float(spectrum[dominant_idx])
        total = float(np.sum(spectrum)) + 1e-9
        ratio = dominant / total
        severity = float(np.clip(ratio, 0.0, 1.0))
        if severity < 0.20:
            return None
        return TemporalPattern(
            kind="periodicity",
            severity=severity,
            details={"dominant_index": dominant_idx, "power_ratio": ratio},
        )

    def _detect_burst_windows(self, events: List[SecurityEvent]) -> TemporalPattern | None:
        if len(events) < 10:
            return None
        ts = [_to_utc(event.timestamp) for event in events]
        ts.sort()
        left = 0
        max_count = 0
        window = timedelta(minutes=30)
        for right in range(len(ts)):
            while ts[right] - ts[left] > window:
                left += 1
            max_count = max(max_count, right - left + 1)

        span_hours = max((ts[-1] - ts[0]).total_seconds() / 3600.0, 0.5)
        baseline = len(ts) / max(span_hours * 2.0, 1.0)  # expected count per 30 min
        if baseline <= 0:
            return None
        ratio = float(max_count) / float(baseline)
        if ratio <= 3.0:
            return None
        severity = float(np.clip(ratio / 6.0, 0.0, 1.0))
        return TemporalPattern(
            kind="burst_window",
            severity=severity,
            details={"max_count": int(max_count), "baseline": float(baseline), "ratio": float(ratio)},
        )


def _bin_counts(events: List[SecurityEvent], *, bucket_minutes: int) -> List[tuple[datetime, int]]:
    if not events:
        return []
    bucket = timedelta(minutes=int(bucket_minutes))
    timestamps = [_to_utc(event.timestamp) for event in events]
    start = _floor_bucket(min(timestamps), bucket_minutes=bucket_minutes)
    end = _floor_bucket(max(timestamps), bucket_minutes=bucket_minutes)
    counts: Dict[datetime, int] = {}
    for stamp in timestamps:
        key = _floor_bucket(stamp, bucket_minutes=bucket_minutes)
        counts[key] = counts.get(key, 0) + 1
    cursor = start
    out: List[tuple[datetime, int]] = []
    while cursor <= end:
        out.append((cursor, counts.get(cursor, 0)))
        cursor = cursor + bucket
    return out


def _floor_bucket(value: datetime, *, bucket_minutes: int) -> datetime:
    dt = _to_utc(value)
    minute = (dt.minute // bucket_minutes) * bucket_minutes
    return dt.replace(minute=minute, second=0, microsecond=0)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
