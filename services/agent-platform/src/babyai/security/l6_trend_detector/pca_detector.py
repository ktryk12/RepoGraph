from __future__ import annotations

from typing import Iterable

import numpy as np
from sklearn.decomposition import PCA

from babyai.security.event_store import SecurityEvent

from .feature_extractor import SecurityFeatureExtractor


class PCABaselineDetector:
    N_COMPONENTS = 8
    ERROR_THRESHOLD = 2.5

    def __init__(self, extractor: SecurityFeatureExtractor | None = None) -> None:
        self.extractor = extractor or SecurityFeatureExtractor()
        self.pca: PCA | None = None
        self.error_median = 0.0
        self.error_std = 1.0
        self._fitted = False

    def fit(self, events: Iterable[SecurityEvent]) -> None:
        rows = list(events)
        if not rows:
            self._fitted = False
            self.pca = None
            return
        self.extractor.fit(rows)
        x = self.extractor.extract_matrix(rows)
        if x.shape[0] < 2:
            self._fitted = False
            self.pca = None
            return
        n_components = int(min(self.N_COMPONENTS, x.shape[0], x.shape[1]))
        if n_components < 1:
            self._fitted = False
            self.pca = None
            return
        self.pca = PCA(n_components=n_components, random_state=42)
        transformed = self.pca.fit_transform(x)
        reconstructed = self.pca.inverse_transform(transformed)
        errors = np.mean((x - reconstructed) ** 2, axis=1)
        self.error_median = float(np.median(errors))
        self.error_std = float(np.std(errors)) + 1e-9
        self._fitted = True

    def score(self, event: SecurityEvent) -> float:
        if not self._fitted or self.pca is None:
            return 0.0
        x = self.extractor.extract(event).reshape(1, -1)
        transformed = self.pca.transform(x)
        reconstructed = self.pca.inverse_transform(transformed)
        error = float(np.mean((x - reconstructed) ** 2))
        z = (error - self.error_median) / self.error_std
        normalized = np.clip(max(0.0, z) / self.ERROR_THRESHOLD, 0.0, 1.0)
        return float(normalized)
