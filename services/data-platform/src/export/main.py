"""
services/data-exporter/main.py — DataExporter service (port 8140).

Pakker fact-check-historik og provenance-data i salgsformater:
  - JSON-LD  (schema.org/ClaimReview — Google-kompatibelt)
  - CSV      (analyst-målgrupper)
  - NDJSON   (data scientists / pipeline-integration)

Læser fra:
  - artifacts/audit/audit.db     (order/trade-historik)
  - artifacts/order_manager/orders.db
  - SQLite provenance store

Eksporterer til artifacts/exports/{format}/{YYYY-MM-DD}/

HTTP endpoints:
  GET /health
  GET /export/claims?format=jsonld|csv|ndjson&from=YYYY-MM-DD&to=YYYY-MM-DD
  GET /export/trades?format=csv|ndjson&from=YYYY-MM-DD&to=YYYY-MM-DD
  GET /exports          — liste over tidligere exports
  POST /export/trigger  — kør eksport nu (alle formater)

Env vars:
  FACT_CHECK_DB   : SQLite med fact-check claims (default: artifacts/fact_check/claims.db)
  AUDIT_DB        : audit SQLite (default: artifacts/audit/audit.db)
  EXPORT_DIR      : eksport-mappe (default: artifacts/exports)
  DATA_EXPORTER_PORT : port (default: 8140)
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import sqlite3
from datetime import datetime, date, timezone, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("data-exporter")

_FACT_CHECK_DB = Path(os.getenv("FACT_CHECK_DB", "artifacts/fact_check/claims.db"))
_AUDIT_DB      = Path(os.getenv("AUDIT_DB", "artifacts/audit/audit.db"))
_EXPORT_DIR    = Path(os.getenv("EXPORT_DIR", "artifacts/exports"))
_PORT          = int(os.getenv("DATA_EXPORTER_PORT", "8140"))

_SCHEMA_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Data readers
# ---------------------------------------------------------------------------

def _read_claims(from_date: Optional[str] = None, to_date: Optional[str] = None) -> List[Dict]:
    if not _FACT_CHECK_DB.exists():
        return _stub_claims()
    try:
        conn  = sqlite3.connect(str(_FACT_CHECK_DB))
        query = "SELECT * FROM claims"
        params: List[str] = []
        if from_date:
            query += " WHERE detected_at >= ?"
            params.append(from_date)
        if to_date:
            query += (" AND" if from_date else " WHERE") + " detected_at <= ?"
            params.append(to_date)
        rows = conn.execute(query, params).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM claims LIMIT 0").description]
        conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        _log.warning("data_exporter_claims_read_error error=%s", exc)
        return _stub_claims()


def _read_audit_events(
    event_type: str = "position.closed",
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> List[Dict]:
    if not _AUDIT_DB.exists():
        return []
    try:
        conn  = sqlite3.connect(str(_AUDIT_DB))
        query = "SELECT * FROM audit_events WHERE event_type=?"
        params: List[Any] = [event_type]
        if from_date:
            query += " AND recorded_at >= ?"
            params.append(from_date)
        if to_date:
            query += " AND recorded_at <= ?"
            params.append(to_date)
        rows = conn.execute(query, params).fetchall()
        cols = [d[0] for d in conn.execute("SELECT * FROM audit_events LIMIT 0").description]
        conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except Exception as exc:
        _log.warning("data_exporter_audit_read_error error=%s", exc)
        return []


def _stub_claims() -> List[Dict]:
    """Sample-data til demo/partnerskab når live DB ikke eksisterer."""
    return [
        {
            "claim_id": f"sample-{i:03d}",
            "raw_text": text,
            "platform": platform,
            "verdict": verdict,
            "confidence": conf,
            "detected_at": f"2026-04-{i+1:02d}T10:00:00+00:00",
            "sources": sources,
        }
        for i, (text, platform, verdict, conf, sources) in enumerate([
            ("Bitcoin will reach $1M by end of 2025", "twitter", "UNVERIFIED", 0.45, ["coindesk.com"]),
            ("ECB cuts rates to 0%", "tiktok", "FALSE", 0.92, ["ecb.europa.eu", "reuters.com"]),
            ("New study shows 95% of crypto projects are scams", "youtube", "MISLEADING", 0.71, ["nature.com"]),
            ("Gold-backed stablecoin launched by EU", "twitter", "FALSE", 0.88, ["europa.eu"]),
            ("DeFi protocol hacked for $500M", "twitter", "TRUE", 0.95, ["coindesk.com", "cointelegraph.com"]),
        ])
    ]


# ---------------------------------------------------------------------------
# Export formatters
# ---------------------------------------------------------------------------

def _to_jsonld(claims: List[Dict]) -> str:
    items = []
    for c in claims:
        item = {
            "@context": "https://schema.org",
            "@type": "ClaimReview",
            "url": f"https://babyai.app/claims/{c.get('claim_id', '')}",
            "claimReviewed": c.get("raw_text", ""),
            "reviewRating": {
                "@type": "Rating",
                "ratingValue": _verdict_to_rating(c.get("verdict", "UNVERIFIED")),
                "bestRating": 5,
                "worstRating": 1,
                "alternateName": c.get("verdict", "UNVERIFIED"),
            },
            "author": {"@type": "Organization", "name": "babyAI Truth Engine"},
            "datePublished": c.get("detected_at", ""),
            "itemReviewed": {
                "@type": "Claim",
                "appearance": {"@type": "SocialMediaPosting", "description": c.get("raw_text", "")},
            },
        }
        items.append(item)
    return json.dumps(items, indent=2, ensure_ascii=False)


def _to_csv(claims: List[Dict]) -> str:
    if not claims:
        return ""
    buf = io.StringIO()
    fields = ["claim_id", "raw_text", "platform", "verdict", "confidence", "detected_at"]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(claims)
    return buf.getvalue()


def _to_ndjson(records: List[Dict]) -> str:
    lines = []
    for r in records:
        # Redact sensitive fields
        clean = {k: v for k, v in r.items() if k not in ("raw_payload",)}
        lines.append(json.dumps(clean, ensure_ascii=False))
    return "\n".join(lines)


def _to_trades_csv(events: List[Dict]) -> str:
    if not events:
        return ""
    buf    = io.StringIO()
    fields = ["order_id", "symbol", "side", "quantity", "price", "pnl", "strategy_id", "recorded_at"]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(events)
    return buf.getvalue()


def _verdict_to_rating(verdict: str) -> int:
    return {"TRUE": 5, "MISLEADING": 3, "UNVERIFIED": 2, "FALSE": 1, "SATIRE": 2}.get(verdict, 2)


# ---------------------------------------------------------------------------
# Export trigger
# ---------------------------------------------------------------------------

def run_full_export(from_date: Optional[str] = None, to_date: Optional[str] = None) -> Dict:
    today     = datetime.now(timezone.utc).date().isoformat()
    from_date = from_date or (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
    to_date   = to_date or today
    claims    = _read_claims(from_date, to_date)
    trades    = _read_audit_events("position.closed", from_date, to_date)
    results   = {}

    for fmt, content, subdir, ext in [
        ("jsonld",  _to_jsonld(claims),      "claims/jsonld",  ".jsonld"),
        ("csv",     _to_csv(claims),         "claims/csv",     ".csv"),
        ("ndjson",  _to_ndjson(claims),      "claims/ndjson",  ".ndjson"),
        ("trades_csv",  _to_trades_csv(trades),  "trades/csv", ".csv"),
        ("trades_ndjson", _to_ndjson(trades), "trades/ndjson", ".ndjson"),
    ]:
        out_dir  = _EXPORT_DIR / subdir
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = out_dir / f"{today}{ext}"
        filename.write_text(content, encoding="utf-8")
        results[fmt] = str(filename)
        _log.info("data_exporter_export fmt=%s rows=%d path=%s",
                  fmt, len(claims) if "trade" not in fmt else len(trades), filename)

    return {
        "ok":        True,
        "from_date": from_date,
        "to_date":   to_date,
        "claims":    len(claims),
        "trades":    len(trades),
        "files":     results,
        "exported_at": datetime.now(timezone.utc).isoformat(),
    }


def list_exports() -> List[Dict]:
    exports = []
    if _EXPORT_DIR.exists():
        for f in sorted(_EXPORT_DIR.rglob("*.*"), reverse=True)[:50]:
            exports.append({
                "path":     str(f.relative_to(_EXPORT_DIR)),
                "size":     f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            })
    return exports


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/health":
            self._json({"ok": True})
            return

        if parsed.path == "/exports":
            self._json(list_exports())
            return

        if parsed.path == "/export/claims":
            fmt       = (params.get("format") or ["jsonld"])[0]
            from_date = (params.get("from") or [None])[0]
            to_date   = (params.get("to") or [None])[0]
            claims    = _read_claims(from_date, to_date)
            if fmt == "csv":
                self._text(_to_csv(claims), "text/csv")
            elif fmt == "ndjson":
                self._text(_to_ndjson(claims), "application/x-ndjson")
            else:
                self._text(_to_jsonld(claims), "application/ld+json")
            return

        if parsed.path == "/export/trades":
            fmt       = (params.get("format") or ["csv"])[0]
            from_date = (params.get("from") or [None])[0]
            to_date   = (params.get("to") or [None])[0]
            trades    = _read_audit_events("position.closed", from_date, to_date)
            if fmt == "ndjson":
                self._text(_to_ndjson(trades), "application/x-ndjson")
            else:
                self._text(_to_trades_csv(trades), "text/csv")
            return

        self._json({"error": "not_found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/export/trigger":
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            body = json.loads(self.rfile.read(content_length)) if content_length else {}
            result = run_full_export(body.get("from_date"), body.get("to_date"))
            self._json(result)
            return
        self._json({"error": "not_found"}, 404)

    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, content: str, content_type: str, status=200):
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    server = HTTPServer(("0.0.0.0", _PORT), Handler)
    _log.info("data_exporter_starting port=%d", _PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log.info("data_exporter_shutting_down")


if __name__ == "__main__":
    main()
