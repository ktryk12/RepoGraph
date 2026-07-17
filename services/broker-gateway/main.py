"""
services/broker-gateway/main.py — BrokerGateway Kafka service.

Kafka consumer: order.intent
Kafka producer: order.executed | order.failed | order.partial

Responsibility:
  - Læs order.intent events
  - Kør pre-trade risk checks (RiskEngine)
  - Route til konfigureret BrokerAdapter (paper / binance / bybit)
  - Emit resultat-event

Env vars:
  KAFKA_BOOTSTRAP_SERVERS : default 127.0.0.1:9092
  BROKER_ADAPTER          : "paper" | "binance" | "bybit"  (default: paper)
  BINANCE_API_KEY / BINANCE_API_SECRET
  BYBIT_API_KEY   / BYBIT_API_SECRET
  KILL_SWITCH             : "1" → afvis alle ordrer straks (nødstop)
  RISK_MAX_ORDER_USDT / RISK_MAX_POSITION_USDT / RISK_MAX_DAILY_LOSS_USDT / RISK_MAX_OPEN_POSITIONS

Topics:
  IN  : order.intent
  OUT : order.executed | order.failed | order.partial
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("broker-gateway")

_BROKERS        = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BROKERS", "127.0.0.1:9092"))
_GROUP_ID       = os.getenv("BROKER_GATEWAY_GROUP", "broker-gateway-service")
_ADAPTER_NAME   = os.getenv("BROKER_ADAPTER", "paper").lower()
_KILL_SWITCH    = os.getenv("KILL_SWITCH", "0") == "1"

_TOPIC_IN          = "order.intent"
_TOPIC_EXECUTED    = "order.executed"
_TOPIC_FAILED      = "order.failed"
_TOPIC_PARTIAL     = "order.partial"


# ---------------------------------------------------------------------------
# Adapter factory
# ---------------------------------------------------------------------------

def _build_adapter():
    if _ADAPTER_NAME == "binance":
        from broker_gateway.adapters.binance_adapter import BinanceAdapter
        api_key    = os.environ["BINANCE_API_KEY"]
        api_secret = os.environ["BINANCE_API_SECRET"]
        adapter = BinanceAdapter(api_key=api_key, api_secret=api_secret)
        _log.info("broker_gateway_adapter=binance testnet=%s", os.getenv("BINANCE_TESTNET", "0"))
        return adapter

    if _ADAPTER_NAME == "bybit":
        from broker_gateway.adapters.bybit_adapter import BybitAdapter
        api_key    = os.environ["BYBIT_API_KEY"]
        api_secret = os.environ["BYBIT_API_SECRET"]
        adapter = BybitAdapter(api_key=api_key, api_secret=api_secret)
        _log.info("broker_gateway_adapter=bybit testnet=%s", os.getenv("BYBIT_TESTNET", "0"))
        return adapter

    from broker_gateway.adapters.paper_adapter import PaperAdapter
    _log.info("broker_gateway_adapter=paper")
    return PaperAdapter()


# ---------------------------------------------------------------------------
# Order processing
# ---------------------------------------------------------------------------

def _process_intent(payload: Dict[str, Any], adapter, risk_engine, producer) -> None:
    from broker_gateway.interfaces.broker_adapter import OrderIntent, OrderSide, OrderType

    order_id   = payload.get("order_id") or str(uuid.uuid4())
    symbol     = str(payload.get("symbol", "")).strip().upper()
    side_raw   = str(payload.get("side", "BUY")).upper()
    type_raw   = str(payload.get("order_type", "MARKET")).upper()
    quantity   = float(payload.get("quantity", 0))
    price      = payload.get("price")
    stop_price = payload.get("stop_price")

    if _KILL_SWITCH:
        _emit(producer, _TOPIC_FAILED, order_id, {
            "order_id":  order_id,
            "symbol":    symbol,
            "status":    "FAILED",
            "reason":    "kill_switch_active",
            "source":    "broker_gateway",
            "timestamp": _now(),
        })
        _log.warning("broker_gateway_kill_switch order_id=%s", order_id)
        return

    if not symbol or quantity <= 0:
        _emit(producer, _TOPIC_FAILED, order_id, {
            "order_id":  order_id,
            "symbol":    symbol,
            "status":    "FAILED",
            "reason":    "invalid_intent: symbol or quantity missing",
            "source":    "broker_gateway",
            "timestamp": _now(),
        })
        return

    try:
        side       = OrderSide(side_raw)
        order_type = OrderType(type_raw)
    except ValueError as exc:
        _emit(producer, _TOPIC_FAILED, order_id, {
            "order_id":  order_id, "symbol": symbol, "status": "FAILED",
            "reason":    f"invalid_side_or_type: {exc}",
            "source":    "broker_gateway", "timestamp": _now(),
        })
        return

    intent = OrderIntent(
        order_id=order_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        price=float(price) if price is not None else None,
        stop_price=float(stop_price) if stop_price is not None else None,
        client_order_id=payload.get("client_order_id"),
        metadata=payload.get("metadata", {}),
    )

    # Pre-trade risk check
    current_price = adapter.get_ticker_price(symbol)
    passed, reason = risk_engine.check(intent, current_price)
    if not passed:
        _emit(producer, _TOPIC_FAILED, order_id, {
            "order_id":  order_id, "symbol": symbol, "side": side_raw,
            "status":    "FAILED", "reason": f"risk_check: {reason}",
            "source":    "broker_gateway", "timestamp": _now(),
        })
        return

    # Submit
    receipt = adapter.submit_order(intent)

    result: Dict[str, Any] = {
        "order_id":           receipt.order_id,
        "exchange_order_id":  receipt.exchange_order_id,
        "symbol":             receipt.symbol,
        "side":               receipt.side.value,
        "order_type":         receipt.order_type.value,
        "status":             receipt.status.value,
        "requested_quantity": receipt.requested_quantity,
        "filled_quantity":    receipt.filled_quantity,
        "average_price":      receipt.average_price,
        "commission":         receipt.commission,
        "submitted_at":       receipt.submitted_at.isoformat(),
        "filled_at":          receipt.filled_at.isoformat() if receipt.filled_at else None,
        "adapter":            adapter.exchange_name,
        "is_paper":           adapter.is_paper(),
        "source":             "broker_gateway",
        "timestamp":          _now(),
        # Pass through strategy metadata for downstream
        "strategy_id":        payload.get("strategy_id", ""),
        "signal_id":          payload.get("signal_id", ""),
    }

    if receipt.is_filled:
        notional = receipt.filled_quantity * receipt.average_price
        risk_engine.record_fill(symbol, side, notional)
        _emit(producer, _TOPIC_EXECUTED, order_id, result)
        _log.info("broker_gateway_executed order_id=%s symbol=%s side=%s fill_price=%.4f",
                  order_id, symbol, side_raw, receipt.average_price)
    elif receipt.status.value == "PARTIAL":
        _emit(producer, _TOPIC_PARTIAL, order_id, result)
        _log.info("broker_gateway_partial order_id=%s filled=%.6f", order_id, receipt.filled_quantity)
    else:
        result["reason"] = str(receipt.raw_response.get("error", receipt.status.value))
        _emit(producer, _TOPIC_FAILED, order_id, result)
        _log.warning("broker_gateway_failed order_id=%s reason=%s", order_id, result["reason"])


def _emit(producer, topic: str, key: str, payload: Dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    producer.produce(topic=topic, key=key.encode(), value=raw)
    producer.flush(timeout=5)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Kafka I/O
# ---------------------------------------------------------------------------

def _build_consumer():
    from confluent_kafka import Consumer
    return Consumer({
        "bootstrap.servers":  _BROKERS,
        "group.id":           _GROUP_ID,
        "auto.offset.reset":  "latest",
        "enable.auto.commit": True,
    })


def _build_producer():
    from confluent_kafka import Producer
    return Producer({"bootstrap.servers": _BROKERS, "acks": "all"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    from broker_gateway.risk_engine import RiskEngine

    adapter     = _build_adapter()
    risk_engine = RiskEngine()
    consumer    = _build_consumer()
    producer    = _build_producer()
    consumer.subscribe([_TOPIC_IN])

    _log.info("broker_gateway_starting adapter=%s kill_switch=%s topic=%s",
              adapter.exchange_name, _KILL_SWITCH, _TOPIC_IN)

    if not adapter.health_check():
        _log.warning("broker_gateway_health_check_failed adapter=%s", adapter.exchange_name)

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                _log.warning("broker_gateway_kafka_error error=%s", msg.error())
                continue
            try:
                payload = json.loads(msg.value().decode("utf-8"))
                _process_intent(payload, adapter, risk_engine, producer)
            except Exception as exc:
                _log.error("broker_gateway_process_error error=%s", exc, exc_info=True)
    except KeyboardInterrupt:
        _log.info("broker_gateway_shutting_down")
    finally:
        consumer.close()
        producer.flush(timeout=5)


if __name__ == "__main__":
    main()
