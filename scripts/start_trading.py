"""
Start BabyAI Trading System.

Requirements:
  - Kafka running (KAFKA_BROKERS or default localhost:9092)
  - Redis running (optional — for idempotency)
  - BINANCE_API_KEY + BINANCE_SECRET_KEY set as env vars

Default mode: PAPER (no real orders placed).
Live mode: set BOTH env vars:
  TRADING_MODE=LIVE
  TRADING_LIVE_CONFIRMED=YES

Usage:
  python scripts/start_trading.py
  TRADING_MODE=LIVE TRADING_LIVE_CONFIRMED=YES python scripts/start_trading.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

# Add repo root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
_log = logging.getLogger("start_trading")


def _build_kafka_producer() -> object:
    """Build confluent_kafka Producer. Returns None if unavailable."""
    brokers = os.getenv("KAFKA_BROKERS", "127.0.0.1:9092")
    try:
        from confluent_kafka import Producer
        p = Producer({"bootstrap.servers": brokers, "client.id": "trading-producer"})
        _log.info("kafka_producer_ready brokers=%s", brokers)
        return p
    except ImportError:
        _log.warning("confluent_kafka not installed — running without Kafka")
        return None
    except Exception as exc:
        _log.warning("kafka_producer_failed error=%s — continuing without Kafka", exc)
        return None


def _print_banner(mode: str) -> None:
    print("╔══════════════════════════════════════════╗")
    print("║     BabyAI Trading System                ║")
    print(f"║     Mode: {mode:<30}║")
    print("║     Symbols: 20 crypto (5m candles)      ║")
    print("║     Strategy: RSI + MACD + SMA crossover ║")
    print("╚══════════════════════════════════════════╝")
    print()


def _confirm_live_mode() -> None:
    """Require manual ENTER confirmation before starting LIVE trading."""
    print("⚠️  LIVE TRADING AKTIVERET — rigtige penge på spil")
    print()
    print("   Circuit breakers:")
    print(f"   Max eksponering:    ${os.getenv('MAX_TOTAL_EXPOSURE_USDT', '500')} USDT")
    print(f"   Max per ordre:      ${os.getenv('MAX_ORDER_USDT', '50')} USDT")
    print(f"   Stop ved tab:       {os.getenv('MAX_DAILY_LOSS_PCT', '3.0')}% per dag")
    print()
    print("   Policy check (validate_live_switch) anbefales kørt separat.")
    print()
    try:
        confirm = input("   Tryk ENTER for at bekræfte og starte LIVE trading... ")
        _ = confirm  # any input accepted
    except (EOFError, KeyboardInterrupt):
        print("\nAnnulleret.")
        sys.exit(0)


async def main() -> int:
    mode = os.getenv("TRADING_MODE", "PAPER").upper().strip()
    confirmed = os.getenv("TRADING_LIVE_CONFIRMED", "").upper().strip()

    _print_banner(mode)

    # Live confirmation
    if mode == "LIVE" and confirmed == "YES":
        _confirm_live_mode()
    elif mode == "LIVE":
        _log.warning(
            "TRADING_MODE=LIVE but TRADING_LIVE_CONFIRMED!=YES — falling back to PAPER"
        )
        mode = "PAPER"
        os.environ["TRADING_MODE"] = "PAPER"

    # Build components
    producer = _build_kafka_producer()

    try:
        from babyai.skills.trading.binance_client import BinanceClientWrapper
        binance = BinanceClientWrapper()
        _log.info("binance_client mode=%s", binance.mode)
    except EnvironmentError as exc:
        _log.error("binance_client_failed: %s", exc)
        print(f"\nError: {exc}")
        print("Set BINANCE_API_KEY and BINANCE_SECRET_KEY env vars.")
        return 1

    from agents.trading_agent import TradingAgent
    from policy.trading_policy import get_trading_policy
    from babyai.skills.trading.binance_stream import BinanceKlineStream
    from babyai.skills.trading.signal_consumer import SignalConsumer

    agent = TradingAgent(
        policy=get_trading_policy(),
        binance_client=binance,
    )
    await agent.initialize()

    stream = BinanceKlineStream(producer)
    consumer = SignalConsumer(agent)

    _log.info("starting WebSocket stream for %d symbols...", len(stream.symbols))
    _log.info("starting signal consumer...")

    # Position management loop (runs every 60s alongside stream)
    async def _position_manager() -> None:
        while True:
            await asyncio.sleep(60)
            try:
                await agent.manage_positions()
            except Exception as exc:
                _log.warning("position_manager_error error=%s", exc)

    try:
        await asyncio.gather(
            stream.start(),
            consumer.start(),
            _position_manager(),
        )
    except KeyboardInterrupt:
        _log.info("shutdown requested")
        stream.stop()
        consumer.stop()

    stats = stream.stats()
    consumer_stats = consumer.stats()
    _log.info(
        "shutdown_complete candles=%d analyses=%d signals_executed=%d",
        stats["candles_received"],
        stats["analyses_triggered"],
        consumer_stats["executed"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
