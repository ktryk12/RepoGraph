"""
BinanceKlineStream — WebSocket 5-minute kline stream for 20 symbols.

Maintains a rolling buffer of up to 100 closed candles per symbol.
On each closed candle:
  1. Publishes raw kline to Kafka topic: trading.market_data
  2. When buffer >= 30 candles: publishes analysis trigger to decision.requested

Requires python-binance >= 1.0.19 (AsyncClient + BinanceSocketManager).

Usage:
    stream = BinanceKlineStream(kafka_producer)
    await stream.start()   # blocks; call stream.stop() from another task
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional

import pandas as pd

_log = logging.getLogger(__name__)

SYMBOLS_20: List[str] = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT",
    "XRPUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
    "UNIUSDT", "ATOMUSDT", "LTCUSDT", "BCHUSDT", "FILUSDT",
    "NEARUSDT", "ICPUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
]

_MAX_BUFFER = 100
_MIN_CANDLES_FOR_ANALYSIS = 30


class BinanceKlineStream:
    """
    Streams 5-minute closed klines from Binance for SYMBOLS_20.
    Publishes to Kafka. Maintains per-symbol rolling buffers.
    """

    def __init__(
        self,
        kafka_producer: Any,
        symbols: List[str] = SYMBOLS_20,
        interval: str = "5m",
    ) -> None:
        self._producer = kafka_producer
        self.symbols = list(symbols)
        self.interval = interval
        self.buffers: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.running = False
        self._candles_received = 0
        self._analyses_triggered = 0

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the WebSocket stream. Blocks until stop() is called."""
        self.running = True
        _log.info(
            "binance_stream_starting symbols=%d interval=%s",
            len(self.symbols), self.interval,
        )
        try:
            from binance import AsyncClient, BinanceSocketManager
            client = await AsyncClient.create(
                os.getenv("BINANCE_API_KEY", ""),
                os.getenv("BINANCE_SECRET_KEY", ""),
            )
            bsm = BinanceSocketManager(client)
            streams = [
                f"{s.lower()}@kline_{self.interval}"
                for s in self.symbols
            ]
            async with bsm.multiplex_socket(streams) as stream:
                _log.info("binance_stream_connected streams=%d", len(streams))
                while self.running:
                    try:
                        msg = await asyncio.wait_for(stream.recv(), timeout=30.0)
                        await self._handle_message(msg)
                    except asyncio.TimeoutError:
                        _log.debug("binance_stream_heartbeat")
                        continue
                    except Exception as exc:
                        _log.warning("binance_stream_recv_error error=%s", exc)
                        await asyncio.sleep(1)
        except Exception as exc:
            _log.error("binance_stream_failed error=%s", exc)
            raise
        finally:
            try:
                await client.close_connection()
            except Exception:
                pass
            _log.info(
                "binance_stream_stopped candles=%d analyses=%d",
                self._candles_received, self._analyses_triggered,
            )

    def stop(self) -> None:
        self.running = False

    # ── Message handling ──────────────────────────────────────────────────────

    async def _handle_message(self, msg: Any) -> None:
        if not isinstance(msg, dict):
            return
        data = msg.get("data", {})
        if not isinstance(data, dict):
            return
        kline = data.get("k", {})
        if not kline:
            return
        if not kline.get("x"):
            # Not a closed candle — ignore
            return
        await self._process_closed_candle(kline)

    async def _process_closed_candle(self, kline: Dict[str, Any]) -> None:
        symbol = str(kline.get("s", ""))
        if not symbol:
            return

        candle: Dict[str, Any] = {
            "symbol": symbol,
            "timestamp": int(kline["t"]),
            "open": float(kline["o"]),
            "high": float(kline["h"]),
            "low": float(kline["l"]),
            "close": float(kline["c"]),
            "volume": float(kline["v"]),
            "interval": self.interval,
        }

        # Update rolling buffer
        buf = self.buffers[symbol]
        buf.append(candle)
        if len(buf) > _MAX_BUFFER:
            buf.pop(0)

        self._candles_received += 1

        # Publish raw kline to Kafka
        self._publish("trading.market_data", candle)

        # Trigger analysis when enough history
        if len(buf) >= _MIN_CANDLES_FOR_ANALYSIS:
            self._trigger_analysis(symbol, buf)

    def _trigger_analysis(self, symbol: str, buf: List[Dict[str, Any]]) -> None:
        msg = {
            "type": "trade_analysis_request",
            "symbol": symbol,
            "candles": buf[-50:],   # send most recent 50 candles
            "source": "binance_websocket",
            "interval": self.interval,
        }
        self._publish("decision.requested", msg)
        self._analyses_triggered += 1

    def _publish(self, topic: str, payload: Dict[str, Any]) -> None:
        if self._producer is None:
            return
        try:
            raw = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            # confluent_kafka Producer.produce() is sync
            if hasattr(self._producer, "produce"):
                self._producer.produce(topic, raw)
                self._producer.poll(0)
            # kafka-python KafkaProducer.send() is also supported
            elif hasattr(self._producer, "send"):
                self._producer.send(topic, raw)
        except Exception as exc:
            _log.warning("kafka_publish_failed topic=%s error=%s", topic, exc)

    # ── Data access ───────────────────────────────────────────────────────────

    def get_dataframe(self, symbol: str) -> pd.DataFrame:
        """Return current buffer for symbol as a DataFrame."""
        buf = self.buffers.get(symbol, [])
        if not buf:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
        df = pd.DataFrame(buf)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df.sort_values("timestamp").reset_index(drop=True)

    def buffer_size(self, symbol: str) -> int:
        return len(self.buffers.get(symbol, []))

    def stats(self) -> Dict[str, Any]:
        return {
            "candles_received": self._candles_received,
            "analyses_triggered": self._analyses_triggered,
            "symbols_with_data": sum(1 for b in self.buffers.values() if b),
        }
