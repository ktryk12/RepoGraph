"""
claim_detector/emitter.py — Kafka producer til topic 'claim.detected'.
Schema: {claim_id, source_url, platform, raw_text, detected_at, virality_score,
         controversy_score, factcheckability_score, composite_score}
"""
from __future__ import annotations

import json
import logging
import os

from claim_detector.models import DetectedClaim

_log = logging.getLogger("claim_detector.emitter")

_BROKERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
_TOPIC   = os.getenv("CLAIM_DETECTED_TOPIC", "claim.detected")


class ClaimEmitter:
    def __init__(self) -> None:
        self._producer = self._build()

    def emit(self, claim: DetectedClaim) -> None:
        if not self._producer:
            _log.info("claim_emitter_stub claim_id=%s platform=%s", claim.claim_id, claim.platform)
            return
        payload = {
            "claim_id":               claim.claim_id,
            "source_url":             claim.source_url,
            "platform":               claim.platform,
            "raw_text":               claim.raw_text,
            "detected_at":            claim.detected_at,
            "virality_score":         claim.virality_score,
            "controversy_score":      claim.controversy_score,
            "factcheckability_score": claim.factcheckability_score,
            "composite_score":        claim.composite_score,
        }
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
        self._producer.produce(
            topic=_TOPIC,
            key=claim.claim_id.encode(),
            value=raw,
        )
        self._producer.poll(0)
        _log.debug("claim_emitted claim_id=%s platform=%s score=%.3f",
                   claim.claim_id, claim.platform, claim.composite_score)

    def flush(self) -> None:
        if self._producer:
            self._producer.flush(timeout=5)

    @staticmethod
    def _build():
        try:
            from confluent_kafka import Producer
            return Producer({"bootstrap.servers": _BROKERS, "acks": "all"})
        except Exception as exc:
            _log.warning("claim_emitter_kafka_unavailable error=%s — stub mode", exc)
            return None
