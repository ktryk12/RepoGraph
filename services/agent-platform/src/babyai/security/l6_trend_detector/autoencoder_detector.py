from __future__ import annotations

from typing import Iterable

import numpy as np
from sklearn.neural_network import MLPRegressor

from babyai.security.event_store import SecurityEvent

from .feature_extractor import SecurityFeatureExtractor
from .pca_detector import PCABaselineDetector


class AutoencoderDetector:
    ERROR_THRESHOLD = 2.5

    def __init__(self, extractor: SecurityFeatureExtractor | None = None) -> None:
        self.extractor = extractor or SecurityFeatureExtractor()
        self.model = MLPRegressor(
            hidden_layer_sizes=(8, 4, 8),
            max_iter=500,
            random_state=42,
        )
        self.error_median = 0.0
        self.error_std = 1.0
        self._fitted = False

    def fit(self, events: Iterable[SecurityEvent]) -> None:
        rows = list(events)
        if not rows:
            self._fitted = False
            return
        self.extractor.fit(rows)
        x = self.extractor.extract_matrix(rows)
        if x.shape[0] < 2:
            self._fitted = False
            return
        self.model.fit(x, x)
        predicted = self.model.predict(x)
        errors = np.mean((x - predicted) ** 2, axis=1)
        self.error_median = float(np.median(errors))
        self.error_std = float(np.std(errors)) + 1e-9
        self._fitted = True

    def score(self, event: SecurityEvent) -> float:
        if not self._fitted:
            return 0.0
        x = self.extractor.extract(event).reshape(1, -1)
        predicted = self.model.predict(x)
        error = float(np.mean((x - predicted) ** 2))
        z = (error - self.error_median) / self.error_std
        return float(np.clip(max(0.0, z) / self.ERROR_THRESHOLD, 0.0, 1.0))

    def combined_score(self, pca: PCABaselineDetector, event: SecurityEvent) -> float:
        pca_score = float(pca.score(event))
        ae_score = float(self.score(event))
        return float(np.clip((0.6 * pca_score) + (0.4 * ae_score), 0.0, 1.0))
