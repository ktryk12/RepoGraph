"""
services/order-manager/main.py — OrderManager Kafka service.

Kafka consumer: order.executed | order.failed | order.partial | signal.generated
Kafka producer: position.opened | position.closed | position.updated | order.intent

Responsibility:
  - Modtag signal.generated fra trading-agenter → valider → emit order.intent til broker-gateway
  - Track ordre-livscyklus: DRAFT → SUBMITTED → FILLED → [STOP_LOSS|TAKE_PROFIT] → CLOSED
  - Administrér stop-loss og take-profit som automatiske follow-up ordrer
  - Persistér state til SQLite (artifact-store)
  - Emit position.opened / position.closed til UI + risk-engine

Env vars:
  KAFKA_BOOTSTRAP_SERVERS  : default 127.0.0.1:9092
  ORDER_MANAGER_DB         : SQLite-sti (default: artifacts/order_manager/orders.db)
  DEFAULT_STOP_LOSS_PCT    : stop-loss som % under entry (default: 0.03 = 3%)
  DEFAULT_TAKE_PROFIT_PCT  : take-profit som % over entry (default: 0.06 = 6%)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("order-manager")

_BROKERS          = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BROKERS", "127.0.0.1:9092"))
_GROUP_ID         = os.getenv("ORDER_MANAGER_GROUP", "order-manager-service")
_DB_PATH          = Path(os.getenv("ORDER_MANAGER_DB", "artifacts/order_manager/orders.db"))
_STOP_LOSS_PCT    = float(os.getenv("DEFAULT_STOP_LOSS_PCT", "0.03"))
_TAKE_PROFIT_PCT  = float(os.getenv("DEFAULT_TAKE_PROFIT_PCT", "0.06"))

_TOPIC_SIGNAL    = "signal.generated"
_TOPIC_EXECUTED  = "order.executed"
_TOPIC_FAILED    = "order.failed"
_TOPIC_PARTIAL   = "order.partial"
_TOPIC_INTENT    = "order.intent"
_TOPIC_POS_OPEN  = "position.opened"
_TOPIC_POS_CLOSE = "position.closed"
_TOPIC_POS_UPD   = "position.updated"


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class OrderState:
    DRAFT     = "DRAFT"
    SUBMITTED = "SUBMITTED"
    FILLED    = "FILLED"
    PARTIAL   = "PARTIAL"
    SL_PLACED = "SL_PLACED"    # stop-loss ordre er sendt
    TP_PLACED = "TP_PLACED"    # take-profit ordre er sendt
    CLOSED    = "CLOSED"
    FAILED    = "FAILED"
    CANCELLED = "CANCELLED"


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------

def _init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id        TEXT PRIMARY KEY,
            signal_id       TEXT,
            strategy_id     TEXT,
            symbol          TEXT NOT NULL,
            side            TEXT NOT NULL,
            quantity        REAL NOT NULL,
            entry_price     REAL,
            stop_loss_price REAL,
            take_profit_price REAL,
            sl_order_id     TEXT,
            tp_order_id     TEXT,
            state           TEXT NOT NULL,
            filled_qty      REAL DEFAULT 0,
            avg_price       REAL DEFAULT 0,
            pnl             REAL DEFAULT 0,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            closed_at       TEXT,
            meta            TEXT DEFAULT '{}'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            position_id  TEXT PRIMARY KEY,
            order_id     TEXT NOT NULL,
            symbol       TEXT NOT NULL,
            quantity     REAL NOT NULL,
            entry_price  REAL NOT NULL,
            current_price REAL DEFAULT 0,
            unrealized_pnl REAL DEFAULT 0,
            opened_at    TEXT NOT NULL,
            closed_at    TEXT,
            is_open      INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    return conn


@contextmanager
def _tx(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _upsert_order(conn, order: Dict[str, Any]) -> None:
    with _tx(conn):
        conn.execute("""
            INSERT INTO orders
              (order_id, signal_id, strategy_id, symbol, side, quantity, entry_price,
               stop_loss_price, take_profit_price, sl_order_id, tp_order_id, state,
               filled_qty, avg_price, pnl, created_at, updated_at, closed_at, meta)
            VALUES
              (:order_id, :signal_id, :strategy_id, :symbol, :side, :quantity, :entry_price,
               :stop_loss_price, :take_profit_price, :sl_order_id, :tp_order_id, :state,
               :filled_qty, :avg_price, :pnl, :created_at, :updated_at, :closed_at, :meta)
            ON CONFLICT(order_id) DO UPDATE SET
              state             = excluded.state,
              entry_price       = excluded.entry_price,
              stop_loss_price   = excluded.stop_loss_price,
              take_profit_price = excluded.take_profit_price,
              sl_order_id       = excluded.sl_order_id,
              tp_order_id       = excluded.tp_order_id,
              filled_qty        = excluded.filled_qty,
              avg_price         = excluded.avg_price,
              pnl               = excluded.pnl,
              updated_at        = excluded.updated_at,
              closed_at         = excluded.closed_at,
              meta              = excluded.meta
        """, order)


def _get_order(conn, order_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in conn.execute("SELECT * FROM orders LIMIT 0").description]
    return dict(zip(cols, row))


def _get_order_by_sl_or_tp(conn, exchange_order_id: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM orders WHERE sl_order_id = ? OR tp_order_id = ?",
        (exchange_order_id, exchange_order_id),
    ).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in conn.execute("SELECT * FROM orders LIMIT 0").description]
    return dict(zip(cols, row))


def _open_position(conn, order: Dict[str, Any]) -> str:
    pos_id = str(uuid.uuid4())
    now = _now()
    with _tx(conn):
        conn.execute("""
            INSERT INTO positions
              (position_id, order_id, symbol, quantity, entry_price, current_price,
               unrealized_pnl, opened_at, is_open)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, 1)
        """, (pos_id, order["order_id"], order["symbol"],
              order["filled_qty"], order["avg_price"], order["avg_price"], now))
    return pos_id


def _close_position(conn, order_id: str, exit_price: float, pnl: float) -> None:
    now = _now()
    with _tx(conn):
        conn.execute("""
            UPDATE positions SET is_open=0, closed_at=?, current_price=?,
              unrealized_pnl=? WHERE order_id=? AND is_open=1
        """, (now, exit_price, pnl, order_id))


# ---------------------------------------------------------------------------
# Signal handler — incoming trade signal → emit order.intent
# ---------------------------------------------------------------------------

def _handle_signal(payload: Dict[str, Any], conn, producer) -> None:
    signal_id   = payload.get("signal_id") or str(uuid.uuid4())
    strategy_id = payload.get("strategy_id", "")
    symbol      = str(payload.get("symbol", "")).upper()
    side        = str(payload.get("side", "BUY")).upper()
    quantity    = float(payload.get("quantity", 0))
    price       = payload.get("price")
    order_type  = str(payload.get("order_type", "MARKET")).upper()

    if not symbol or quantity <= 0:
        _log.warning("order_manager_signal_invalid signal_id=%s", signal_id)
        return

    order_id = str(uuid.uuid4())
    now = _now()

    # Compute SL/TP from signal overrides or defaults
    entry_est  = float(price) if price else 0.0
    sl_price   = float(payload["stop_loss_price"]) if payload.get("stop_loss_price") else (
        round(entry_est * (1 - _STOP_LOSS_PCT), 8) if entry_est and side == "BUY" else 0.0
    )
    tp_price   = float(payload["take_profit_price"]) if payload.get("take_profit_price") else (
        round(entry_est * (1 + _TAKE_PROFIT_PCT), 8) if entry_est and side == "BUY" else 0.0
    )

    order = {
        "order_id":          order_id,
        "signal_id":         signal_id,
        "strategy_id":       strategy_id,
        "symbol":            symbol,
        "side":              side,
        "quantity":          quantity,
        "entry_price":       None,
        "stop_loss_price":   sl_price or None,
        "take_profit_price": tp_price or None,
        "sl_order_id":       None,
        "tp_order_id":       None,
        "state":             OrderState.DRAFT,
        "filled_qty":        0.0,
        "avg_price":         0.0,
        "pnl":               0.0,
        "created_at":        now,
        "updated_at":        now,
        "closed_at":         None,
        "meta":              json.dumps({"source": "signal", "raw": payload}),
    }
    _upsert_order(conn, order)

    # Emit to broker-gateway
    intent: Dict[str, Any] = {
        "order_id":    order_id,
        "signal_id":   signal_id,
        "strategy_id": strategy_id,
        "symbol":      symbol,
        "side":        side,
        "order_type":  order_type,
        "quantity":    quantity,
        "source":      "order_manager",
        "timestamp":   now,
    }
    if price:
        intent["price"] = float(price)

    _emit(producer, _TOPIC_INTENT, order_id, intent)
    _log.info("order_manager_signal_submitted order_id=%s symbol=%s side=%s qty=%.6f",
              order_id, symbol, side, quantity)

    # Update state to SUBMITTED
    order["state"]      = OrderState.SUBMITTED
    order["updated_at"] = _now()
    _upsert_order(conn, order)


# ---------------------------------------------------------------------------
# order.executed handler — place SL/TP follow-up orders
# ---------------------------------------------------------------------------

def _handle_executed(payload: Dict[str, Any], conn, producer) -> None:
    order_id    = payload.get("order_id", "")
    avg_price   = float(payload.get("average_price", 0))
    filled_qty  = float(payload.get("filled_quantity", 0))
    exchange_oid = payload.get("exchange_order_id", "")
    symbol      = payload.get("symbol", "")
    side        = payload.get("side", "BUY")

    order = _get_order(conn, order_id)

    if order is None:
        # Kan være et SL/TP-fill — tjek om det er parent-ordren
        order = _get_order_by_sl_or_tp(conn, exchange_oid)
        if order:
            _handle_sl_tp_filled(order, payload, avg_price, filled_qty, conn, producer)
            return
        _log.warning("order_manager_executed_unknown order_id=%s", order_id)
        return

    now = _now()
    order["state"]      = OrderState.FILLED
    order["filled_qty"] = filled_qty
    order["avg_price"]  = avg_price
    order["entry_price"] = avg_price
    order["updated_at"] = now

    # Åbn position
    pos_id = _open_position(conn, order)
    _upsert_order(conn, order)

    _emit(producer, _TOPIC_POS_OPEN, order_id, {
        "order_id":     order_id,
        "position_id":  pos_id,
        "symbol":       symbol,
        "side":         side,
        "quantity":     filled_qty,
        "entry_price":  avg_price,
        "strategy_id":  order.get("strategy_id", ""),
        "signal_id":    order.get("signal_id", ""),
        "opened_at":    now,
        "source":       "order_manager",
        "timestamp":    now,
    })
    _log.info("order_manager_position_opened order_id=%s symbol=%s entry=%.4f qty=%.6f",
              order_id, symbol, avg_price, filled_qty)

    # Place stop-loss follow-up
    if order.get("stop_loss_price") and side == "BUY":
        _place_stop_loss(order, avg_price, conn, producer)

    # Place take-profit follow-up
    if order.get("take_profit_price") and side == "BUY":
        _place_take_profit(order, avg_price, conn, producer)


def _place_stop_loss(order: Dict, entry_price: float, conn, producer) -> None:
    sl_price  = float(order["stop_loss_price"])
    sl_oid    = str(uuid.uuid4())
    now       = _now()

    _emit(producer, _TOPIC_INTENT, sl_oid, {
        "order_id":    sl_oid,
        "parent_order_id": order["order_id"],
        "symbol":      order["symbol"],
        "side":        "SELL",
        "order_type":  "STOP_LOSS",
        "quantity":    order["filled_qty"],
        "stop_price":  sl_price,
        "source":      "order_manager",
        "timestamp":   now,
    })
    order["sl_order_id"] = sl_oid
    order["state"]       = OrderState.SL_PLACED
    order["updated_at"]  = now
    _upsert_order(conn, order)
    _log.info("order_manager_sl_placed order_id=%s sl_price=%.4f", order["order_id"], sl_price)


def _place_take_profit(order: Dict, entry_price: float, conn, producer) -> None:
    tp_price  = float(order["take_profit_price"])
    tp_oid    = str(uuid.uuid4())
    now       = _now()

    _emit(producer, _TOPIC_INTENT, tp_oid, {
        "order_id":    tp_oid,
        "parent_order_id": order["order_id"],
        "symbol":      order["symbol"],
        "side":        "SELL",
        "order_type":  "LIMIT",
        "quantity":    order["filled_qty"],
        "price":       tp_price,
        "source":      "order_manager",
        "timestamp":   now,
    })
    order["tp_order_id"] = tp_oid
    order["state"]       = OrderState.TP_PLACED
    order["updated_at"]  = now
    _upsert_order(conn, order)
    _log.info("order_manager_tp_placed order_id=%s tp_price=%.4f", order["order_id"], tp_price)


def _handle_sl_tp_filled(
    parent_order: Dict, payload: Dict, exit_price: float, filled_qty: float, conn, producer
) -> None:
    now     = _now()
    entry   = float(parent_order.get("avg_price") or 0)
    qty     = float(parent_order.get("filled_qty") or filled_qty)
    pnl     = (exit_price - entry) * qty if entry else 0.0
    symbol  = parent_order["symbol"]
    is_sl   = parent_order.get("sl_order_id") == payload.get("exchange_order_id") or \
              parent_order.get("sl_order_id") == payload.get("order_id")

    parent_order["state"]      = OrderState.CLOSED
    parent_order["pnl"]        = pnl
    parent_order["closed_at"]  = now
    parent_order["updated_at"] = now
    _upsert_order(conn, parent_order)
    _close_position(conn, parent_order["order_id"], exit_price, pnl)

    _emit(producer, _TOPIC_POS_CLOSE, parent_order["order_id"], {
        "order_id":    parent_order["order_id"],
        "symbol":      symbol,
        "exit_price":  exit_price,
        "entry_price": entry,
        "quantity":    qty,
        "pnl":         pnl,
        "close_reason": "stop_loss" if is_sl else "take_profit",
        "strategy_id": parent_order.get("strategy_id", ""),
        "signal_id":   parent_order.get("signal_id", ""),
        "closed_at":   now,
        "source":      "order_manager",
        "timestamp":   now,
    })
    _log.info("order_manager_position_closed order_id=%s pnl=%.4f reason=%s",
              parent_order["order_id"], pnl, "stop_loss" if is_sl else "take_profit")


# ---------------------------------------------------------------------------
# order.failed handler
# ---------------------------------------------------------------------------

def _handle_failed(payload: Dict[str, Any], conn) -> None:
    order_id = payload.get("order_id", "")
    order    = _get_order(conn, order_id)
    if order is None:
        return
    now = _now()
    order["state"]      = OrderState.FAILED
    order["updated_at"] = now
    order["meta"]       = json.dumps({"reason": payload.get("reason", ""), "raw": payload})
    _upsert_order(conn, order)
    _log.warning("order_manager_order_failed order_id=%s reason=%s",
                 order_id, payload.get("reason", ""))


# ---------------------------------------------------------------------------
# order.partial handler
# ---------------------------------------------------------------------------

def _handle_partial(payload: Dict[str, Any], conn, producer) -> None:
    order_id   = payload.get("order_id", "")
    filled_qty = float(payload.get("filled_quantity", 0))
    avg_price  = float(payload.get("average_price", 0))
    order      = _get_order(conn, order_id)
    if order is None:
        return
    now = _now()
    order["state"]      = OrderState.PARTIAL
    order["filled_qty"] = filled_qty
    order["avg_price"]  = avg_price
    order["updated_at"] = now
    _upsert_order(conn, order)
    _emit(producer, _TOPIC_POS_UPD, order_id, {
        "order_id":    order_id,
        "symbol":      payload.get("symbol", ""),
        "filled_qty":  filled_qty,
        "avg_price":   avg_price,
        "state":       "PARTIAL",
        "source":      "order_manager",
        "timestamp":   now,
    })
    _log.info("order_manager_partial order_id=%s filled=%.6f", order_id, filled_qty)


# ---------------------------------------------------------------------------
# Kafka helpers
# ---------------------------------------------------------------------------

def _emit(producer, topic: str, key: str, payload: Dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    producer.produce(topic=topic, key=key.encode(), value=raw)
    producer.flush(timeout=5)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    conn     = _init_db(_DB_PATH)
    consumer = _build_consumer()
    producer = _build_producer()
    consumer.subscribe([_TOPIC_SIGNAL, _TOPIC_EXECUTED, _TOPIC_FAILED, _TOPIC_PARTIAL])

    _log.info("order_manager_starting db=%s topics=%s",
              _DB_PATH, [_TOPIC_SIGNAL, _TOPIC_EXECUTED, _TOPIC_FAILED, _TOPIC_PARTIAL])

    _HANDLERS = {
        _TOPIC_SIGNAL:   lambda p: _handle_signal(p, conn, producer),
        _TOPIC_EXECUTED: lambda p: _handle_executed(p, conn, producer),
        _TOPIC_FAILED:   lambda p: _handle_failed(p, conn),
        _TOPIC_PARTIAL:  lambda p: _handle_partial(p, conn, producer),
    }

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                _log.warning("order_manager_kafka_error error=%s", msg.error())
                continue
            try:
                topic   = msg.topic()
                payload = json.loads(msg.value().decode("utf-8"))
                handler = _HANDLERS.get(topic)
                if handler:
                    handler(payload)
            except Exception as exc:
                _log.error("order_manager_process_error topic=%s error=%s", topic, exc, exc_info=True)
    except KeyboardInterrupt:
        _log.info("order_manager_shutting_down")
    finally:
        consumer.close()
        producer.flush(timeout=5)
        conn.close()


if __name__ == "__main__":
    main()
