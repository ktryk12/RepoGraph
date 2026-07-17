"""
CryptoIntelAgent — autonomous market intelligence polling agent.

Runs a 60-second loop that:
  1. Fetches market snapshot, trending/whale overlap, new token candidates
  2. Deduplicates via Redis (TTL=300s per signal hash)
  3. Publishes typed signals to Kafka topics:
       signal.crypto.whale      — whale transactions > $1 M
       signal.crypto.market     — full market snapshot + trending
       signal.crypto.newproject — new token candidates scored ≥ 0.70

Signal format
-------------
{
  "source":           "crypto_intel_agent",
  "timestamp":        "<ISO8601>",
  "signal_type":      "whale" | "market" | "newproject",
  "confidence":       0.0–1.0,
  "data":             { … },
  "requires_action":  true | false
}
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents._kafka_publisher import _KafkaPublisher

# Kafka topic constants — mirrors bus/topics.py (avoid importing the bus
# package here since its __init__ pulls in babyai_shared at import time,
# which would break standalone test invocations without the shared package
# installed.  The canonical definitions live in bus/topics.py.)
SIGNAL_CRYPTO_WHALE      = "signal.crypto.whale"
SIGNAL_CRYPTO_MARKET     = "signal.crypto.market"
SIGNAL_CRYPTO_NEWPROJECT = "signal.crypto.newproject"

from tools.crypto_intel.aggregator import CryptoIntelAggregator

_log = logging.getLogger(__name__)

_POLL_INTERVAL = 60          # seconds between polling cycles
_DEDUP_TTL     = 300         # Redis key TTL in seconds
_MIN_CONFIDENCE = 0.70       # minimum confidence to publish newproject signals
_REDIS_URL     = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_BROKERS       = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BROKERS", "127.0.0.1:9092"))


# ---------------------------------------------------------------------------
# Signal builder
# ---------------------------------------------------------------------------

def _build_signal(
    signal_type: str,
    data: Any,
    confidence: float,
    requires_action: bool = False,
) -> Dict[str, Any]:
    return {
        "source":          "crypto_intel_agent",
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "signal_type":     signal_type,
        "confidence":      round(confidence, 4),
        "data":            data,
        "requires_action": requires_action,
    }


def _signal_hash(signal: Dict[str, Any]) -> str:
    """Stable hash over (signal_type, data) — ignores timestamp."""
    key = json.dumps(
        {"t": signal["signal_type"], "d": signal["data"]},
        sort_keys=True,
        separators=(",", ":"),
    )
    return "crypto_intel:" + hashlib.sha256(key.encode()).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Redis dedup helper
# ---------------------------------------------------------------------------

class _RedisDedup:
    """
    Thin Redis-backed deduplication gate.
    Falls back to an in-process dict when Redis is unavailable.
    """

    def __init__(self, url: str = _REDIS_URL) -> None:
        self._redis: Optional[Any] = None
        self._fallback: Dict[str, float] = {}
        try:
            import redis as _redis_lib
            client = _redis_lib.from_url(url, decode_responses=True, socket_connect_timeout=2)
            client.ping()
            self._redis = client
            _log.info("crypto_intel_dedup redis_connected url=%s", url)
        except Exception as exc:
            _log.warning("crypto_intel_dedup redis_unavailable error=%s — using in-process fallback", exc)

    def is_duplicate(self, key: str) -> bool:
        """Return True if this key was seen within TTL, else record it."""
        if self._redis is not None:
            try:
                return not bool(self._redis.set(key, "1", ex=_DEDUP_TTL, nx=True))
            except Exception as exc:
                _log.warning("crypto_intel_dedup redis_error error=%s", exc)
        # in-process fallback
        now = time.monotonic()
        if key in self._fallback and now - self._fallback[key] < _DEDUP_TTL:
            return True
        self._fallback[key] = now
        return False


# ---------------------------------------------------------------------------
# CryptoIntelAgent
# ---------------------------------------------------------------------------

class CryptoIntelAgent:
    """
    Autonomous polling agent for crypto market intelligence.

    Runs in a background daemon thread.  Call start() to begin the loop
    and stop() to request graceful shutdown.

    Example
    -------
    >>> agent = CryptoIntelAgent()
    >>> agent.start()   # non-blocking
    >>> ...
    >>> agent.stop()
    """

    agent_id: str = "crypto-intel-001"
    role:     str = "market-intelligence"

    def __init__(
        self,
        aggregator:  CryptoIntelAggregator | None = None,
        dedup:       _RedisDedup             | None = None,
        publisher:   _KafkaPublisher         | None = None,
        poll_interval: float = _POLL_INTERVAL,
        market_data_adapter: Optional[Any] = None,
    ) -> None:
        self._agg      = aggregator   or CryptoIntelAggregator()
        self._dedup    = dedup        or _RedisDedup()
        self._pub      = publisher    or _KafkaPublisher()
        self._interval = poll_interval
        self._thread:  Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        # Market data integration via Kafka (replaces direct OpenBB calls)
        # TODO: Initialize Kafka producer for market.data.requested.v1
        # TODO: Initialize Kafka consumer for market.data.received.v1
        self._market_data_enabled = False  # Enable when Kafka integration is complete

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start background polling thread (non-blocking)."""
        if self._thread and self._thread.is_alive():
            _log.warning("crypto_intel_agent already running")
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="crypto-intel-agent",
            daemon=True,
        )
        self._thread.start()
        _log.info("crypto_intel_agent started interval=%ss", self._interval)

    def stop(self) -> None:
        """Request graceful shutdown."""
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=10)
        _log.info("crypto_intel_agent stopped")

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self._poll_cycle()
            except Exception as exc:
                _log.error("crypto_intel_cycle_error error=%s", exc, exc_info=True)
            self._stop_evt.wait(timeout=self._interval)

    def _poll_cycle(self) -> None:
        _log.debug("crypto_intel_cycle start")

        # 1. Market snapshot → signal.crypto.market
        self._emit_market_snapshot()

        # 2. Whale/trending overlap → signal.crypto.whale
        self._emit_whale_overlap()

        # 3. New token candidates → signal.crypto.newproject (confidence gate)
        self._emit_new_token_candidates()

        self._pub.flush()
        _log.debug("crypto_intel_cycle done")

    # ── Market data integration ───────────────────────────────────────────────

    async def _request_market_data(self, symbol: str, data_type: str, parameters: Optional[Dict] = None) -> Optional[Dict]:
        """
        Request market data via Kafka adapter (replaces direct OpenBB calls).

        TODO: Implement actual Kafka request/response pattern:
        1. Publish to market.data.requested.v1
        2. Wait for response on market.data.received.v1 or market.data.failed.v1
        3. Return provider-neutral data

        For now, returns None to maintain graceful degradation.
        """
        if not self._market_data_enabled:
            _log.debug("Market data integration disabled - skipping %s request for %s", data_type, symbol)
            return None

        # TODO: Implement Kafka request pattern
        # correlation_id = str(uuid.uuid4())
        # request = {
        #     "symbol": symbol,
        #     "data_type": data_type,
        #     "parameters": parameters or {},
        #     "correlation_id": correlation_id
        # }
        # await self._market_data_producer.send("market.data.requested.v1", request)
        # response = await self._wait_for_response(correlation_id, timeout=5)
        # return response.get("data") if response else None

        return None

    # ── Emitters ──────────────────────────────────────────────────────────────

    def _emit_market_snapshot(self) -> None:
        try:
            snapshot = self._agg.get_market_snapshot()
        except Exception as exc:
            _log.warning("crypto_intel_snapshot_failed error=%s", exc)
            return

        # Optional market data enrichment via Kafka adapter — never blocks main flow
        if self._market_data_enabled:
            try:
                # TODO: Replace with async Kafka calls when integration is complete
                # macro = await self._request_market_data("FEDFUNDS", "macro_indicator")
                # if macro:
                #     snapshot["macro_fedfunds"] = macro
                pass
            except Exception as exc:
                _log.warning("crypto_intel_macro_skipped error=%s", exc)
            try:
                # TODO: Replace with async Kafka calls when integration is complete
                # for etf_sym in ("IBIT", "FBTC"):
                #     insider = await self._request_market_data(etf_sym, "sec_insider_trades", {"limit": 5})
                #     if insider:
                #         snapshot.setdefault("sec_insider_trades", {})[etf_sym] = insider
                pass
            except Exception as exc:
                _log.warning("crypto_intel_insider_skipped error=%s", exc)

        signal = _build_signal(
            signal_type="market",
            data=snapshot,
            confidence=0.80,
            requires_action=False,
        )
        self._maybe_publish(SIGNAL_CRYPTO_MARKET, "market-snapshot", signal)

    def _emit_whale_overlap(self) -> None:
        try:
            overlaps = self._agg.get_trending_with_whale_overlap()
        except Exception as exc:
            _log.warning("crypto_intel_whale_overlap_failed error=%s", exc)
            return

        if not overlaps:
            return

        for item in overlaps:
            coin_id = (item.get("coin") or {}).get("id", "unknown")
            score   = item.get("score", 1)
            # Confidence: 1 whale txn = 0.70, each additional +0.05, cap 0.95
            confidence = min(0.70 + 0.05 * (score - 1), 0.95)
            signal = _build_signal(
                signal_type="whale",
                data=item,
                confidence=confidence,
                requires_action=confidence >= 0.80,
            )
            self._maybe_publish(SIGNAL_CRYPTO_WHALE, f"whale-{coin_id}", signal)

    def _emit_new_token_candidates(self) -> None:
        try:
            candidates = self._agg.get_new_token_candidates()
        except Exception as exc:
            _log.warning("crypto_intel_candidates_failed error=%s", exc)
            return

        for candidate in candidates:
            score = candidate.get("score", 0.0)
            if score < _MIN_CONFIDENCE:
                continue  # confidence gate

            coin_id = (candidate.get("coin") or {}).get("id", "unknown")
            signal  = _build_signal(
                signal_type="newproject",
                data=candidate,
                confidence=score,
                requires_action=score >= 0.85,
            )
            self._maybe_publish(
                SIGNAL_CRYPTO_NEWPROJECT, f"newproject-{coin_id}", signal
            )

    # ── Dedup + publish ───────────────────────────────────────────────────────

    def _maybe_publish(self, topic: str, key: str, signal: Dict[str, Any]) -> None:
        h = _signal_hash(signal)
        if self._dedup.is_duplicate(h):
            _log.debug("crypto_intel_dedup_skip topic=%s key=%s", topic, key)
            return
        _log.info(
            "crypto_intel_publish topic=%s key=%s confidence=%.2f",
            topic, key, signal["confidence"],
        )
        self._pub.publish(topic, key, signal)
