from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from babyai.security.event_store import EventStore, SecurityEvent

from .autoencoder_detector import AutoencoderDetector
from .pca_detector import PCABaselineDetector
from .temporal_analyzer import TemporalAnalyzer, TemporalPattern

logger = logging.getLogger(__name__)


class ThreatIntelligence(BaseModel):
    source_event: Optional[Dict[str, Any]] = None
    anomaly_score: float = 0.0
    temporal_pattern: Optional[Dict[str, Any]] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TrendDetector:
    RUN_INTERVAL_MINUTES = 15
    ANOMALY_THRESHOLD = 0.65
    TREND_THRESHOLD = 0.55
    MIN_EVENTS = 50
    CHANNEL = "babyai:threat_intel"

    def __init__(
        self,
        *,
        event_store: EventStore,
        redis_client: Any,
        pca_detector: PCABaselineDetector | None = None,
        autoencoder_detector: AutoencoderDetector | None = None,
        temporal_analyzer: TemporalAnalyzer | None = None,
    ) -> None:
        self.event_store = event_store
        self.redis_client = redis_client
        self.pca_detector = pca_detector or PCABaselineDetector()
        self.autoencoder_detector = autoencoder_detector or AutoencoderDetector()
        self.temporal_analyzer = temporal_analyzer or TemporalAnalyzer()
        self.last_run: datetime | None = None
        self._trained = False

    async def run_loop(self) -> None:
        while True:
            await self.run_cycle()
            await asyncio.sleep(self.RUN_INTERVAL_MINUTES * 60)

    async def run_cycle(self) -> Dict[str, Any]:
        events = await self.event_store.get_recent(days=7)
        if len(events) < self.MIN_EVENTS:
            return {"status": "too_few_events", "count": len(events)}

        if not self._trained:
            self.pca_detector.fit(events)
            self.autoencoder_detector.fit(events)
            self._trained = True

        if self.last_run is None:
            new_events = list(events)
        else:
            new_events = await self.event_store.get_since(self.last_run)

        emitted = 0
        for event in new_events:
            score = self.autoencoder_detector.combined_score(self.pca_detector, event)
            if score <= self.ANOMALY_THRESHOLD:
                continue
            intel = ThreatIntelligence(
                source_event=_event_payload(event),
                anomaly_score=float(score),
                temporal_pattern=None,
            )
            await self._publish(intel)
            emitted += 1

        patterns = self.temporal_analyzer.analyze(events)
        for pattern in patterns:
            if float(pattern.severity) <= self.TREND_THRESHOLD:
                continue
            intel = ThreatIntelligence(
                source_event=None,
                anomaly_score=0.0,
                temporal_pattern=_pattern_payload(pattern),
            )
            await self._publish(intel)
            emitted += 1

        self.last_run = datetime.now(timezone.utc)
        return {"status": "ok", "events_scored": len(new_events), "emitted": emitted}

    async def _publish(self, intel: ThreatIntelligence) -> None:
        if hasattr(intel, "model_dump_json"):
            payload = intel.model_dump_json()
        else:
            payload = json.dumps(intel.dict(), ensure_ascii=True)
        publish = getattr(self.redis_client, "publish", None)
        if not callable(publish):
            return
        result = publish(self.CHANNEL, payload)
        if asyncio.iscoroutine(result):
            await result
        logger.info(
            "security_event event_type=trend_threat_intel_published channel=%s score=%.3f",
            self.CHANNEL,
            float(intel.anomaly_score),
        )


def _event_payload(event: SecurityEvent) -> Dict[str, Any]:
    if hasattr(event, "model_dump"):
        return dict(event.model_dump())
    return dict(event.dict())


def _pattern_payload(pattern: TemporalPattern) -> Dict[str, Any]:
    return {
        "kind": str(pattern.kind),
        "severity": float(pattern.severity),
        "details": dict(pattern.details),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
