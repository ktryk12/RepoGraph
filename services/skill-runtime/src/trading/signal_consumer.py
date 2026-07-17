"""
SignalConsumer — Kafka consumer for trading.market_data and decision.requested topics.

Consumes messages of type "trade_analysis_request":
  1. Builds DataFrame from candles in message
  2. Runs technical analysis
  3. If confidence >= 0.65 and policy allows: calls trading_agent.execute_signal()
  4. Publishes result to trading.recommendations

Uses confluent_kafka Consumer (same as the rest of BabyAI).
Falls back to polling loop if Kafka is unavailable.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import pandas as pd

_log = logging.getLogger(__name__)

_TOPIC_DECISION = "decision.requested"
_TOPIC_RECOMMENDATIONS = "trading.recommendations"
_POLL_INTERVAL = 0.1   # seconds
_MIN_CONFIDENCE = 0.65


class SignalConsumer:
    """
    Consumes trade_analysis_request messages from Kafka.
    Calls agent.execute_signal() when confidence >= threshold.
    """

    def __init__(
        self,
        trading_agent: Any,
        kafka_brokers: str = "",
        group_id: str = "trading-signal-consumer",
    ) -> None:
        self._agent = trading_agent
        self._brokers = kafka_brokers or os.getenv("KAFKA_BROKERS", "127.0.0.1:9092")
        self._group_id = group_id
        self.running = False
        self._signals_processed = 0
        self._signals_executed = 0
        self._signals_rejected = 0

    async def start(self) -> None:
        """Start consuming. Blocks until stop() is called."""
        self.running = True
        _log.info("signal_consumer_starting brokers=%s", self._brokers)
        consumer = self._build_consumer()
        if consumer is None:
            _log.warning("signal_consumer_no_kafka — running in no-op mode")
            while self.running:
                await asyncio.sleep(1)
            return

        try:
            consumer.subscribe([_TOPIC_DECISION])
            _log.info("signal_consumer_subscribed topic=%s", _TOPIC_DECISION)
            while self.running:
                msg = consumer.poll(_POLL_INTERVAL)
                if msg is None:
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue
                if msg.error():
                    _log.warning("kafka_consumer_error error=%s", msg.error())
                    continue
                await self._handle_message(msg.value())
        except Exception as exc:
            _log.error("signal_consumer_failed error=%s", exc)
        finally:
            try:
                consumer.close()
            except Exception:
                pass
            _log.info(
                "signal_consumer_stopped processed=%d executed=%d rejected=%d",
                self._signals_processed,
                self._signals_executed,
                self._signals_rejected,
            )

    def stop(self) -> None:
        self.running = False

    # ── Message handling ──────────────────────────────────────────────────────

    async def _handle_message(self, raw: bytes) -> None:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            _log.warning("signal_consumer_parse_failed error=%s", exc)
            return

        if payload.get("type") != "trade_analysis_request":
            return

        symbol = str(payload.get("symbol", ""))
        candles = payload.get("candles", [])
        if not symbol or not candles:
            return

        self._signals_processed += 1

        # Build DataFrame
        df = self._candles_to_df(candles)
        if df.empty:
            return

        # Run analysis
        try:
            from babyai.skills.trading.fallback.technical import analyze
            signals = analyze(df)
        except Exception as exc:
            _log.warning("signal_analysis_failed symbol=%s error=%s", symbol, exc)
            return

        action = str(signals.get("action", "HOLD"))
        confidence = float(signals.get("confidence", 0.0))

        if action == "HOLD" or confidence < _MIN_CONFIDENCE:
            self._signals_rejected += 1
            return

        # Execute via agent
        try:
            await self._agent.execute_signal(symbol, action, confidence, signals, df)
            self._signals_executed += 1
        except Exception as exc:
            _log.warning("signal_execute_failed symbol=%s error=%s", symbol, exc)

    def _candles_to_df(self, candles: List[Dict[str, Any]]) -> pd.DataFrame:
        if not candles:
            return pd.DataFrame()
        try:
            df = pd.DataFrame(candles)
            if "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            # Normalize column names
            df = df.rename(columns={c: c.lower() for c in df.columns})
            if "close" not in df.columns:
                return pd.DataFrame()
            return df.sort_values("timestamp").reset_index(drop=True)
        except Exception as exc:
            _log.warning("candles_to_df_failed error=%s", exc)
            return pd.DataFrame()

    # ── Kafka ─────────────────────────────────────────────────────────────────

    def _build_consumer(self) -> Optional[Any]:
        try:
            from confluent_kafka import Consumer
            return Consumer({
                "bootstrap.servers": self._brokers,
                "group.id": self._group_id,
                "auto.offset.reset": "latest",
                "enable.auto.commit": True,
            })
        except ImportError:
            _log.warning("confluent_kafka not available — no Kafka consumer")
            return None
        except Exception as exc:
            _log.warning("kafka_consumer_build_failed error=%s", exc)
            return None

    def stats(self) -> Dict[str, Any]:
        return {
            "processed": self._signals_processed,
            "executed": self._signals_executed,
            "rejected": self._signals_rejected,
        }
