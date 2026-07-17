from __future__ import annotations

from datetime import timezone
from hashlib import sha256
import re
from typing import Iterable, List

import numpy as np
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer

from babyai.security.event_store import SecurityEvent, SecurityEventType


class SecurityFeatureExtractor:
    VECTOR_DIM = 25
    _PATTERN_DIM = 7

    def __init__(self) -> None:
        self.vectorizer = TfidfVectorizer(max_features=50)
        self.svd: TruncatedSVD | None = None
        self._svd_components = 0
        self._fitted = False

    def fit(self, events: Iterable[SecurityEvent]) -> None:
        patterns = [str(event.pattern or "") for event in list(events)]
        if not patterns or len(set(patterns)) < 2:
            self._fitted = False
            self.svd = None
            self._svd_components = 0
            return
        matrix = self.vectorizer.fit_transform(patterns)
        n_samples, n_features = int(matrix.shape[0]), int(matrix.shape[1])
        n_components = min(self._PATTERN_DIM, n_features - 1, n_samples - 1)
        if n_components < 1:
            self._fitted = False
            self.svd = None
            self._svd_components = 0
            return
        self.svd = TruncatedSVD(n_components=int(n_components), random_state=42)
        self.svd.fit(matrix)
        self._fitted = True
        self._svd_components = int(n_components)

    def extract(self, event: SecurityEvent) -> np.ndarray:
        out = np.zeros((self.VECTOR_DIM,), dtype=np.float64)
        ts = _to_utc(event.timestamp)
        out[0] = float(ts.hour) / 23.0
        out[1] = float(ts.weekday()) / 6.0
        out[2] = float(max(0, int(event.layer))) / 7.0
        out[3] = _clip01(float(event.severity))
        out[4:8] = _source_one_hot(event.source)
        out[8:12] = _event_type_one_hot(event.event_type)
        snippet = str(event.raw_snippet or "")
        out[12] = min(1.0, len(snippet) / 200.0)
        out[13] = 1.0 if re.search(r"%[0-9a-fA-F]{2}", snippet) else 0.0
        out[14] = 1.0 if re.search(r"<[^>]+>", snippet) else 0.0
        out[15] = 1.0 if _looks_like_json(snippet) else 0.0
        out[16] = min(1.0, len(event.agent_ids) / 20.0)
        out[17] = _domain_bucket(event.domain)
        out[18:25] = self._pattern_projection(str(event.pattern or ""))
        return out

    def extract_matrix(self, events: Iterable[SecurityEvent]) -> np.ndarray:
        rows = [self.extract(event) for event in list(events)]
        if not rows:
            return np.zeros((0, self.VECTOR_DIM), dtype=np.float64)
        return np.vstack(rows)

    def _pattern_projection(self, pattern: str) -> np.ndarray:
        projection = np.zeros((self._PATTERN_DIM,), dtype=np.float64)
        if not self._fitted or self.svd is None:
            return projection
        try:
            tfidf = self.vectorizer.transform([str(pattern or "")])
            reduced = self.svd.transform(tfidf)[0]
        except Exception:
            return projection
        usable = min(self._PATTERN_DIM, len(reduced))
        projection[:usable] = reduced[:usable]
        return projection


def _source_one_hot(source: str) -> np.ndarray:
    text = str(source or "").strip().lower()
    out = np.zeros((4,), dtype=np.float64)
    if any(token in text for token in ("input", "user", "request")):
        out[0] = 1.0
    elif any(token in text for token in ("output", "model", "agent")):
        out[1] = 1.0
    elif any(token in text for token in ("rationale", "reason")):
        out[2] = 1.0
    else:
        out[3] = 1.0
    return out


def _event_type_one_hot(event_type: SecurityEventType) -> np.ndarray:
    out = np.zeros((4,), dtype=np.float64)
    mapping = {
        SecurityEventType.INJECTION_DETECTED: 0,
        SecurityEventType.OUTPUT_INVALID: 1,
        SecurityEventType.RATIONALE_FLAGGED: 2,
        SecurityEventType.ANOMALY_VOTES: 3,
    }
    idx = mapping.get(event_type)
    if idx is not None:
        out[idx] = 1.0
    return out


def _looks_like_json(value: str) -> bool:
    text = str(value or "")
    return ("{" in text and "}" in text) or ('"' in text and ":" in text)


def _domain_bucket(domain: str) -> float:
    digest = sha256(str(domain or "").encode("utf-8")).hexdigest()
    bucket = int(digest, 16) % 100
    return float(bucket) / 100.0


def _clip01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def _to_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
