"""
services/execution-audit/main.py — ExecutionAudit daemon.

Kafka consumer: order.intent | order.executed | order.failed | order.partial
               | position.opened | position.closed | signal.generated

Responsibility:
  - Immutabelt audit-spor: alle order-events skrives append-only til SQLite
  - Ingen events slettes eller overskrives nogensinde
  - Daglig P&L-rapport genereres som JSON-artifact til artifacts/audit/reports/
  - HTTP /health + /report/today + /report/{date} til inspektion

Env vars:
  KAFKA_BOOTSTRAP_SERVERS : default 127.0.0.1:9092
  AUDIT_DB                : SQLite-sti (default: artifacts/audit/audit.db)
  AUDIT_REPORT_DIR        : rapport-mappe (default: artifacts/audit/reports)
  AUDIT_REPORT_HOUR       : time på dagen (UTC) hvor daglig rapport genereres (default: 0)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, date, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("execution-audit")

_BROKERS     = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BROKERS", "127.0.0.1:9092"))
_GROUP_ID    = os.getenv("AUDIT_GROUP", "execution-audit-service")
_DB_PATH     = Path(os.getenv("AUDIT_DB", "artifacts/audit/audit.db"))
_REPORT_DIR  = Path(os.getenv("AUDIT_REPORT_DIR", "artifacts/audit/reports"))
_REPORT_HOUR = int(os.getenv("AUDIT_REPORT_HOUR", "0"))
_PORT        = int(os.getenv("AUDIT_PORT", "8133"))

_TOPICS = [
    "signal.generated",
    "order.intent",
    "order.executed",
    "order.failed",
    "order.partial",
    "position.opened",
    "position.closed",
]


# ---------------------------------------------------------------------------
# Immutable SQLite store
# ---------------------------------------------------------------------------

def _init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    # Append-only event log — never updated or deleted
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type  TEXT NOT NULL,
            order_id    TEXT,
            signal_id   TEXT,
            symbol      TEXT,
            side        TEXT,
            quantity    REAL,
            price       REAL,
            pnl         REAL,
            strategy_id TEXT,
            adapter     TEXT,
            is_paper    INTEGER,
            reason      TEXT,
            raw_payload TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        )
    """)
    # Daily P&L summary — one row per day, regenerated
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_pnl (
            report_date     TEXT PRIMARY KEY,
            total_trades    INTEGER DEFAULT 0,
            winning_trades  INTEGER DEFAULT 0,
            losing_trades   INTEGER DEFAULT 0,
            gross_pnl       REAL DEFAULT 0,
            commission_paid REAL DEFAULT 0,
            net_pnl         REAL DEFAULT 0,
            win_rate        REAL DEFAULT 0,
            largest_win     REAL DEFAULT 0,
            largest_loss    REAL DEFAULT 0,
            symbols_traded  TEXT DEFAULT '[]',
            strategies_used TEXT DEFAULT '[]',
            paper_trades    INTEGER DEFAULT 0,
            live_trades     INTEGER DEFAULT 0,
            generated_at    TEXT NOT NULL
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


def _append_event(conn: sqlite3.Connection, event_type: str, payload: Dict[str, Any]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _tx(conn):
        conn.execute("""
            INSERT INTO audit_events
              (event_type, order_id, signal_id, symbol, side, quantity, price, pnl,
               strategy_id, adapter, is_paper, reason, raw_payload, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            event_type,
            payload.get("order_id"),
            payload.get("signal_id"),
            payload.get("symbol"),
            payload.get("side"),
            _float(payload.get("quantity") or payload.get("filled_quantity") or payload.get("requested_quantity")),
            _float(payload.get("average_price") or payload.get("entry_price") or payload.get("price")),
            _float(payload.get("pnl")),
            payload.get("strategy_id"),
            payload.get("adapter"),
            1 if payload.get("is_paper") else 0,
            payload.get("reason"),
            json.dumps(payload, ensure_ascii=True),
            now,
        ))


def _float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Daily P&L report
# ---------------------------------------------------------------------------

def generate_daily_report(conn: sqlite3.Connection, report_date: Optional[date] = None) -> Dict[str, Any]:
    d = report_date or datetime.now(timezone.utc).date()
    date_str    = d.isoformat()
    next_str    = (d + timedelta(days=1)).isoformat()
    now         = datetime.now(timezone.utc).isoformat()

    rows = conn.execute("""
        SELECT pnl, price, quantity, strategy_id, adapter, is_paper, symbol
        FROM audit_events
        WHERE event_type = 'position.closed'
          AND recorded_at >= ? AND recorded_at < ?
    """, (date_str, next_str)).fetchall()

    total = len(rows)
    wins  = [r for r in rows if (r[0] or 0) > 0]
    losses = [r for r in rows if (r[0] or 0) < 0]
    gross_pnl  = sum(r[0] or 0 for r in rows)
    commission = 0.0  # broker-gateway emits commission on order.executed
    comm_rows  = conn.execute("""
        SELECT SUM(price) FROM audit_events
        WHERE event_type = 'order.executed'
          AND recorded_at >= ? AND recorded_at < ?
    """, (date_str, next_str)).fetchone()
    # commission is embedded in avg_price difference; approximate from order.executed rows
    commission_rows = conn.execute("""
        SELECT raw_payload FROM audit_events
        WHERE event_type = 'order.executed'
          AND recorded_at >= ? AND recorded_at < ?
    """, (date_str, next_str)).fetchall()
    for (raw,) in commission_rows:
        try:
            commission += float(json.loads(raw).get("commission", 0))
        except Exception:
            pass

    symbols    = list({r[6] for r in rows if r[6]})
    strategies = list({r[3] for r in rows if r[3]})
    paper_cnt  = sum(1 for r in rows if r[5])
    live_cnt   = total - paper_cnt

    report = {
        "report_date":    date_str,
        "total_trades":   total,
        "winning_trades": len(wins),
        "losing_trades":  len(losses),
        "gross_pnl":      round(gross_pnl, 6),
        "commission_paid": round(commission, 6),
        "net_pnl":        round(gross_pnl - commission, 6),
        "win_rate":       round(len(wins) / total, 4) if total else 0.0,
        "largest_win":    round(max((r[0] or 0 for r in wins), default=0.0), 6),
        "largest_loss":   round(min((r[0] or 0 for r in losses), default=0.0), 6),
        "symbols_traded": symbols,
        "strategies_used": strategies,
        "paper_trades":   paper_cnt,
        "live_trades":    live_cnt,
        "generated_at":   now,
    }

    with _tx(conn):
        conn.execute("""
            INSERT INTO daily_pnl
              (report_date, total_trades, winning_trades, losing_trades, gross_pnl,
               commission_paid, net_pnl, win_rate, largest_win, largest_loss,
               symbols_traded, strategies_used, paper_trades, live_trades, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_date) DO UPDATE SET
              total_trades=excluded.total_trades, winning_trades=excluded.winning_trades,
              losing_trades=excluded.losing_trades, gross_pnl=excluded.gross_pnl,
              commission_paid=excluded.commission_paid, net_pnl=excluded.net_pnl,
              win_rate=excluded.win_rate, largest_win=excluded.largest_win,
              largest_loss=excluded.largest_loss, symbols_traded=excluded.symbols_traded,
              strategies_used=excluded.strategies_used, paper_trades=excluded.paper_trades,
              live_trades=excluded.live_trades, generated_at=excluded.generated_at
        """, (
            report["report_date"], report["total_trades"], report["winning_trades"],
            report["losing_trades"], report["gross_pnl"], report["commission_paid"],
            report["net_pnl"], report["win_rate"], report["largest_win"],
            report["largest_loss"], json.dumps(symbols), json.dumps(strategies),
            report["paper_trades"], report["live_trades"], now,
        ))

    # Write artifact
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)
    artifact = _REPORT_DIR / f"pnl_{date_str}.json"
    artifact.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _log.info("audit_report_generated date=%s net_pnl=%.4f trades=%d path=%s",
              date_str, report["net_pnl"], total, artifact)
    return report


def _get_report(conn: sqlite3.Connection, report_date: str) -> Optional[Dict]:
    row = conn.execute(
        "SELECT * FROM daily_pnl WHERE report_date = ?", (report_date,)
    ).fetchone()
    if row is None:
        return None
    cols = [d[0] for d in conn.execute("SELECT * FROM daily_pnl LIMIT 0").description]
    data = dict(zip(cols, row))
    for key in ("symbols_traded", "strategies_used"):
        try:
            data[key] = json.loads(data[key])
        except Exception:
            data[key] = []
    return data


# ---------------------------------------------------------------------------
# Daily report scheduler (background thread)
# ---------------------------------------------------------------------------

def _report_scheduler(conn: sqlite3.Connection) -> None:
    last_generated: Optional[date] = None
    while True:
        now = datetime.now(timezone.utc)
        today = now.date()
        if now.hour == _REPORT_HOUR and last_generated != today:
            try:
                generate_daily_report(conn, today)
                last_generated = today
            except Exception as exc:
                _log.error("audit_report_scheduler_error error=%s", exc)
        threading.Event().wait(60)


# ---------------------------------------------------------------------------
# HTTP server for inspection
# ---------------------------------------------------------------------------

def _make_handler(conn: sqlite3.Connection):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            from urllib.parse import urlparse
            path = urlparse(self.path).path

            if path == "/health":
                self._json({"ok": True, "db": str(_DB_PATH)})
                return

            if path == "/report/today":
                today = datetime.now(timezone.utc).date().isoformat()
                data  = _get_report(conn, today)
                if data is None:
                    data = generate_daily_report(conn)
                self._json(data)
                return

            if path.startswith("/report/"):
                date_part = path.removeprefix("/report/").strip("/")
                data = _get_report(conn, date_part)
                if data is None:
                    self._json({"error": "not_found"}, 404)
                else:
                    self._json(data)
                return

            if path == "/events/recent":
                rows = conn.execute("""
                    SELECT event_type, order_id, symbol, side, pnl, recorded_at
                    FROM audit_events ORDER BY id DESC LIMIT 50
                """).fetchall()
                self._json([{
                    "event_type": r[0], "order_id": r[1], "symbol": r[2],
                    "side": r[3], "pnl": r[4], "recorded_at": r[5],
                } for r in rows])
                return

            self._json({"error": "not_found"}, 404)

        def _json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    conn = _init_db(_DB_PATH)

    # Daily report scheduler
    t = threading.Thread(target=_report_scheduler, args=(conn,), daemon=True)
    t.start()

    # HTTP server
    server = HTTPServer(("0.0.0.0", _PORT), _make_handler(conn))
    st = threading.Thread(target=server.serve_forever, daemon=True)
    st.start()
    _log.info("execution_audit_http port=%d", _PORT)

    # Kafka consumer
    from confluent_kafka import Consumer
    consumer = Consumer({
        "bootstrap.servers":  _BROKERS,
        "group.id":           _GROUP_ID,
        "auto.offset.reset":  "earliest",  # audit should never miss events
        "enable.auto.commit": True,
    })
    consumer.subscribe(_TOPICS)
    _log.info("execution_audit_starting db=%s topics=%s", _DB_PATH, _TOPICS)

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                _log.warning("audit_kafka_error error=%s", msg.error())
                continue
            try:
                event_type = msg.topic()
                payload    = json.loads(msg.value().decode("utf-8"))
                _append_event(conn, event_type, payload)
            except Exception as exc:
                _log.error("audit_process_error error=%s", exc, exc_info=True)
    except KeyboardInterrupt:
        _log.info("execution_audit_shutting_down")
    finally:
        consumer.close()
        conn.close()


if __name__ == "__main__":
    main()
