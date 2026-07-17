"""
TrendScoutAgent — listens for analysis signals, scores content opportunities,
and emits content.opportunity.detected when score ≥ 0.60.

Kafka consumer on:
  - signal.analysis.complete  (from DeepAnalysisAgent)
  - signal.crypto.newproject  (from CryptoIntelAgent, fallback path)

Kafka producer to:
  - content.opportunity.detected

L7 boundary — requires_action is ALWAYS False.
No content is created here. Human must approve the resulting brief.

Run as a background thread; start via TrendScoutAgent.start().
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents._kafka_publisher import _KafkaPublisher

_log = logging.getLogger(__name__)

# Inline topic constants — avoid bus/__init__ → babyai_shared chain
_TOPIC_ANALYSIS_COMPLETE  = "signal.analysis.complete"
_TOPIC_CRYPTO_NEWPROJECT  = "signal.crypto.newproject"
_TOPIC_OPPORTUNITY        = "content.opportunity.detected"

_BROKERS      = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BROKERS", "127.0.0.1:9092"))
_GROUP_ID     = os.getenv("TREND_SCOUT_GROUP", "trend-scout-agent")
_POLL_TIMEOUT = float(os.getenv("TREND_SCOUT_POLL_TIMEOUT", "1.0"))


# ---------------------------------------------------------------------------
# TrendScoutAgent
# ---------------------------------------------------------------------------

class TrendScoutAgent:
    """
    Polls signal topics, scores each candidate, emits opportunity signals.

    Attributes:
        agent_id : str  — fixed identifier
        role     : str  — capability label
    """

    agent_id: str = "trend-scout-001"
    role:     str = "trend-scout"

    def __init__(
        self,
        publisher:         Any | None = None,
        opportunity_scorer: Any | None = None,
        review_miner:      Any | None = None,
    ) -> None:
        self._pub     = publisher           # injected in tests; real = _KafkaPublisher()
        self._scorer  = opportunity_scorer  # injected in tests; real = module import
        self._miner   = review_miner        # injected in tests; real = module import
        self._seen:   List[str] = []        # track recently seen topics for novelty bonus

        self._thread:   Optional[threading.Thread] = None
        self._stop_evt  = threading.Event()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start background consumer thread (non-blocking)."""
        if self._thread and self._thread.is_alive():
            _log.warning("trend_scout_agent already running")
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="trend-scout-agent", daemon=True
        )
        self._thread.start()
        _log.info("trend_scout_agent started")

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=15)
        _log.info("trend_scout_agent stopped")

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── Processing ────────────────────────────────────────────────────────────

    def process_signal(self, signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Process one incoming signal. Returns opportunity dict if emitted, else None.
        Safe to call directly (used in tests and ContentOrchestratorAgent).
        """
        signal_type = signal.get("signal_type", "")
        symbol      = self._extract_symbol(signal)

        if not symbol:
            _log.debug("trend_scout_skip_no_symbol signal_type=%s", signal_type)
            return None

        # Score opportunity
        scorer = self._scorer or _import_scorer()
        miner  = self._miner  or _import_miner()

        sentiment = miner.mine_reviews(symbol, category="crypto", symbol=symbol) if miner else {}
        scored    = scorer.score_opportunity(
            analysis_result   = signal,
            sentiment_result  = sentiment,
            signal_confidence = float(signal.get("original_confidence", signal.get("confidence", 0.0))),
            seen_topics       = self._seen,
        )

        if not scored.get("above_threshold", False):
            _log.debug(
                "trend_scout_below_threshold symbol=%s score=%.3f",
                symbol, scored.get("score", 0.0),
            )
            return None

        opportunity = self._build_opportunity(signal, symbol, scored)
        self._seen.append(symbol)
        if len(self._seen) > 200:
            self._seen = self._seen[-100:]

        self._emit(opportunity)
        return opportunity

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self) -> None:
        consumer = _build_consumer()
        if consumer is None:
            _log.warning("trend_scout_no_kafka_consumer — agent idle")
            return

        try:
            consumer.subscribe([_TOPIC_ANALYSIS_COMPLETE, _TOPIC_CRYPTO_NEWPROJECT])
            _log.info("trend_scout_subscribed topics=%s", [_TOPIC_ANALYSIS_COMPLETE, _TOPIC_CRYPTO_NEWPROJECT])

            while not self._stop_evt.is_set():
                msg = consumer.poll(_POLL_TIMEOUT)
                if msg is None:
                    continue
                if msg.error():
                    _log.warning("trend_scout_kafka_error error=%s", msg.error())
                    continue
                try:
                    payload = json.loads(msg.value().decode("utf-8"))
                    self.process_signal(payload)
                except Exception as exc:
                    _log.error("trend_scout_process_error error=%s", exc, exc_info=True)
        finally:
            consumer.close()

    def _build_opportunity(
        self,
        signal:   Dict[str, Any],
        symbol:   str,
        scored:   Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "source":             "trend_scout_agent",
            "opportunity_id":     str(uuid.uuid4()),
            "detected_at":        datetime.now(timezone.utc).isoformat(),
            "symbol":             symbol,
            "signal_type":        signal.get("signal_type", ""),
            "opportunity_score":  scored["score"],
            "verdict":            scored["verdict"],
            "recommendation":     scored["recommendation"],
            "scoring_factors":    scored["factors"],
            "thesis":             signal.get("thesis", ""),
            "original_signal":    {
                "analysis_id":  signal.get("analysis_id", ""),
                "confidence":   signal.get("original_confidence", signal.get("confidence", 0.0)),
            },
            "requires_action":         False,   # L7: ALWAYS False
            "requires_human_review":   True,
        }

    def _emit(self, opportunity: Dict[str, Any]) -> None:
        pub = self._pub or _get_publisher()
        if pub is None:
            _log.info(
                "trend_scout_emit_no_kafka opportunity_id=%s symbol=%s score=%.3f",
                opportunity["opportunity_id"], opportunity["symbol"], opportunity["opportunity_score"],
            )
            return
        try:
            pub.publish(
                topic=_TOPIC_OPPORTUNITY,
                key=opportunity["opportunity_id"],
                value=opportunity,
            )
            pub.flush()
            _log.info(
                "trend_scout_emitted opportunity_id=%s symbol=%s score=%.3f",
                opportunity["opportunity_id"], opportunity["symbol"], opportunity["opportunity_score"],
            )
        except Exception as exc:
            _log.error("trend_scout_emit_failed error=%s", exc)

    @staticmethod
    def _extract_symbol(signal: Dict[str, Any]) -> str:
        # DeepAnalysisAgent format
        if symbol := signal.get("symbol"):
            return symbol
        # CryptoIntelAgent format
        if coin := signal.get("data", {}).get("coin", {}):
            return coin.get("symbol", coin.get("id", ""))
        return ""


# ---------------------------------------------------------------------------
# Module-level lazy helpers
# ---------------------------------------------------------------------------

_publisher_instance: Any = None


def _get_publisher() -> Any:
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = _KafkaPublisher()
    return _publisher_instance if _publisher_instance is not False else None


def _import_scorer():
    try:
        import tools.opportunity_scorer as m
        return m
    except Exception:
        return None


def _import_miner():
    try:
        import tools.review_miner as m
        return m
    except Exception:
        return None


def _build_consumer():
    try:
        from confluent_kafka import Consumer
        return Consumer({
            "bootstrap.servers":  _BROKERS,
            "group.id":           _GROUP_ID,
            "auto.offset.reset":  "latest",
            "enable.auto.commit": True,
        })
    except Exception as exc:
        _log.warning("trend_scout_consumer_unavailable error=%s", exc)
        return None

