"""
DeepAnalysisAgent — triggered analysis for pre-screened candidates.

Triggered ONLY when a candidate with confidence >= 0.70 is received on:
  - signal.crypto.newproject
  - signal.institutional.move  (future)

Per candidate:
  1. Fetch web context via FirecrawlClient (whitepaper / project page)
  2. Get fundamental data via market-data-adapter (equity quote / crypto prices)
  3. Run FinRobotAdapter analysis
  4. Generate investment thesis
  5. Publish enriched signal to: signal.analysis.complete

L7 boundary — requires_action is ALWAYS False.
requires_human_review is ALWAYS True.
No automatic trade proposals.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Kafka topic constants — inline to avoid bus/__init__ → babyai_shared chain
SIGNAL_CRYPTO_NEWPROJECT   = "signal.crypto.newproject"
SIGNAL_INSTITUTIONAL_MOVE  = "signal.institutional.move"
SIGNAL_ANALYSIS_COMPLETE   = "signal.analysis.complete"
SIGNAL_ANALYSIS_FAILED     = "signal.analysis.failed"

_log = logging.getLogger(__name__)

_MIN_CONFIDENCE = 0.70
_BROKERS        = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BROKERS", "127.0.0.1:9092"))


# ---------------------------------------------------------------------------
# Kafka publisher (same pattern as other agents)
# ---------------------------------------------------------------------------

class _Publisher:
    def __init__(self, brokers: str = _BROKERS) -> None:
        self._producer: Optional[Any] = None
        try:
            from confluent_kafka import Producer  # noqa: PLC0415
            self._producer = Producer({"bootstrap.servers": brokers, "acks": "all"})
        except Exception as exc:
            _log.warning("deep_analysis_kafka_unavailable error=%s", exc)

    def publish(self, topic: str, key: str, value: Dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if self._producer is None:
            _log.info("deep_analysis_nopublish topic=%s key=%s", topic, key)
            return
        try:
            self._producer.produce(topic=topic, key=key.encode("utf-8"), value=payload)
            self._producer.poll(0)
        except Exception as exc:
            _log.error("deep_analysis_publish_failed topic=%s error=%s", topic, exc)

    def flush(self) -> None:
        if self._producer:
            try:
                self._producer.flush(timeout=5)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# DeepAnalysisAgent
# ---------------------------------------------------------------------------

class DeepAnalysisAgent:
    """
    Triggered deep analysis agent for pre-screened candidates.

    Not a polling agent — call analyze(payload) directly or wire into
    the supervisor routing layer.

    L7 boundary:
    - requires_action is ALWAYS False
    - requires_human_review is ALWAYS True

    Example::

        agent = DeepAnalysisAgent()
        result = agent.analyze({
            "signal_type": "newproject",
            "confidence":  0.85,
            "data": {"coin": {"id": "sometoken"}, "score": 0.85}
        })
    """

    agent_id: str = "deep-analysis-001"
    role:     str = "deep-analysis"

    def __init__(
        self,
        publisher:  _Publisher | None = None,
        firecrawl:  Any | None = None,
        market_data_adapter:     Any | None = None,
        finrobot:   Any | None = None,
    ) -> None:
        self._pub      = publisher or _Publisher()
        # All three data clients are lazy-initialised on first use
        self._fc       = firecrawl   # FirecrawlClient | None
        self._market_data_enabled = False  # TODO: Enable when Kafka integration complete
        self._finrobot = finrobot    # FinRobotAdapter | None
        self._lock     = threading.Lock()

    # ── Lazy client accessors ─────────────────────────────────────────────────

    def _get_firecrawl(self) -> Any:
        if self._fc is None:
            try:
                from tools.firecrawl_client import FirecrawlClient  # noqa: PLC0415
                self._fc = FirecrawlClient()
            except Exception as exc:
                _log.warning("deep_analysis_firecrawl_init_failed error=%s", exc)
                self._fc = False
        return self._fc if self._fc is not False else None

    async def _request_market_data(self, symbol: str, data_type: str, parameters: Optional[Dict] = None) -> Optional[Dict]:
        """
        Request market data via Kafka adapter (replaces OpenBB calls).

        TODO: Implement Kafka request/response pattern for market data.
        For now returns None to maintain graceful degradation.
        """
        if not self._market_data_enabled:
            _log.debug("Market data integration disabled - skipping %s for %s", data_type, symbol)
            return None

        # TODO: Implement Kafka market data request pattern
        return None

    def _get_finrobot(self) -> Any:
        if self._finrobot is None:
            try:
                from tools.finrobot_adapter import FinRobotAdapter  # noqa: PLC0415
                self._finrobot = FinRobotAdapter()
            except Exception as exc:
                _log.warning("deep_analysis_finrobot_init_failed error=%s", exc)
                self._finrobot = False
        return self._finrobot if self._finrobot is not False else None

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(self, signal_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run deep analysis on a pre-screened signal.

        Returns the enriched analysis dict and publishes to
        signal.analysis.complete (or signal.analysis.failed on error).

        Always returns a dict — never raises.
        """
        analysis_id = str(uuid.uuid4())
        signal_type = signal_payload.get("signal_type", "unknown")
        confidence  = float(signal_payload.get("confidence", 0.0))
        data        = signal_payload.get("data", {})

        if confidence < _MIN_CONFIDENCE:
            _log.debug(
                "deep_analysis_skip signal_type=%s confidence=%.2f below threshold",
                signal_type, confidence,
            )
            return {}

        try:
            return self._run_analysis(analysis_id, signal_type, confidence, data)
        except Exception as exc:
            _log.error("deep_analysis_unexpected_error error=%s", exc, exc_info=True)
            failed = self._build_failed(analysis_id, signal_type, confidence, str(exc))
            self._pub.publish(SIGNAL_ANALYSIS_FAILED, analysis_id, failed)
            self._pub.flush()
            return failed

    def _run_analysis(
        self,
        analysis_id: str,
        signal_type: str,
        confidence:  float,
        data:        Dict[str, Any],
    ) -> Dict[str, Any]:
        symbol       = self._extract_symbol(signal_type, data)
        project_url  = data.get("project_url") or data.get("url")
        data_sources: List[str] = []

        # 1. Web context via Firecrawl
        whitepaper_text: Optional[str] = None
        fc = self._get_firecrawl()
        if fc and project_url:
            page = fc.scrape_crypto_project(project_url)
            if page.get("content"):
                whitepaper_text = page["content"]
                data_sources.append("firecrawl")

        # 2. Fundamental data via market-data-adapter
        fundamental: Dict[str, Any] = {}
        if self._market_data_enabled:
            if signal_type in ("newproject", "whale"):
                # For crypto use crypto price via Kafka
                crypto_sym = data.get("coin", {}).get("symbol", "").upper()
                if crypto_sym:
                    # TODO: Replace with actual Kafka request when integration complete
                    # price_data = await self._request_market_data(f"{crypto_sym}-USD", "crypto_price")
                    # if price_data:
                    #     fundamental.update(price_data)
                    #     data_sources.append("market-data-adapter")
                    pass
            else:
                # TODO: Replace with actual Kafka request when integration complete
                # quote = await self._request_market_data(symbol, "equity_quote")
                # if quote:
                #     fundamental.update(quote)
                #     data_sources.append("market-data-adapter")
                pass

        # 3. FinRobot analysis
        fr     = self._get_finrobot()
        analysis: Dict[str, Any] = {}
        if fr:
            if signal_type in ("newproject",):
                project_name = (
                    data.get("coin", {}).get("name")
                    or data.get("coin", {}).get("id")
                    or symbol
                )
                analysis = fr.analyze_crypto_project(project_name, whitepaper_text)
            else:
                analysis = fr.analyze_equity(symbol)
            if analysis:
                data_sources.append("finrobot")

        # 4. Investment thesis
        thesis_data = {**fundamental, **analysis}
        thesis_data["analysis_score"] = analysis.get("score", 0.0)
        thesis_data["verdict"]        = analysis.get("verdict", "weak")
        thesis_data["risks"]          = analysis.get("risks", [])
        thesis_data["opportunities"]  = analysis.get("opportunities", [])

        thesis = ""
        if fr:
            whale_data = data if signal_type == "whale" else None
            thesis = fr.generate_investment_thesis(symbol, thesis_data, whale_data)
            if thesis:
                data_sources.append("finrobot-thesis")

        # 5. Build and publish result
        result = {
            "source":              "deep_analysis_agent",
            "analysis_id":        analysis_id,
            "timestamp":           datetime.now(timezone.utc).isoformat(),
            "signal_type":         "analysis_complete",
            "symbol":              symbol,
            "original_confidence": round(confidence, 4),
            "analysis_score":      round(analysis.get("score", 0.0), 4),
            "verdict":             analysis.get("verdict", "weak"),
            "thesis":              thesis,
            "data_sources":        list(dict.fromkeys(data_sources)),  # dedupe, preserve order
            "requires_action":     False,    # L7: ALWAYS False
            "requires_human_review": True,   # ALWAYS True
        }

        _log.info(
            "deep_analysis_complete analysis_id=%s symbol=%s verdict=%s score=%.2f",
            analysis_id, symbol, result["verdict"], result["analysis_score"],
        )
        self._pub.publish(SIGNAL_ANALYSIS_COMPLETE, analysis_id, result)
        self._pub.flush()
        return result

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _extract_symbol(self, signal_type: str, data: Dict[str, Any]) -> str:
        if signal_type in ("newproject", "whale"):
            coin = data.get("coin") or {}
            return (
                coin.get("symbol")
                or coin.get("id")
                or data.get("symbol", "UNKNOWN")
            ).upper()
        return data.get("symbol", data.get("ticker", "UNKNOWN")).upper()

    def _build_failed(
        self,
        analysis_id: str,
        signal_type: str,
        confidence:  float,
        error:       str,
    ) -> Dict[str, Any]:
        return {
            "source":              "deep_analysis_agent",
            "analysis_id":        analysis_id,
            "timestamp":           datetime.now(timezone.utc).isoformat(),
            "signal_type":         "analysis_failed",
            "original_confidence": round(confidence, 4),
            "error":               error,
            "requires_action":     False,
            "requires_human_review": True,
        }
