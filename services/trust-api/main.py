"""
services/trust-api/main.py — Trust Score API (port 8141).

Public-facing API til tredjeparts-integration.

Endpoints:
  POST /trust/score        — body: {claim_text, context?} → {score, verdict, sources, evidence_summary}
  GET  /trust/claim/{id}   — slå op i fact-check-historik
  GET  /health
  GET  /metrics            — Prometheus-format

Rate-limiting: per API-key, tiered:
  free  : 100 req/dag
  basic : 1000 req/dag
  pro   : ubegrænset

Auth: X-API-Key header

Env vars:
  TRUST_API_PORT         : port (default: 8141)
  TRUST_API_DB           : SQLite (default: artifacts/trust_api/trust.db)
  FACT_CHECK_DB          : claims SQLite (default: artifacts/fact_check/claims.db)
  ANTHROPIC_API_KEY      : til live scoring via Claude
  CLAUDE_MODEL           : model (default: claude-haiku-4-5-20251001 — hurtig + billig)
  TRUST_API_DEV_KEY      : dev API-key der altid virker (default: dev-trust-key)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, date, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("trust-api")

_PORT          = int(os.getenv("TRUST_API_PORT", "8141"))
_DB_PATH       = Path(os.getenv("TRUST_API_DB", "artifacts/trust_api/trust.db"))
_FACT_CHECK_DB = Path(os.getenv("FACT_CHECK_DB", "artifacts/fact_check/claims.db"))
_CLAUDE_MODEL  = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
_DEV_KEY       = os.getenv("TRUST_API_DEV_KEY", "dev-trust-key")

_RATE_LIMITS = {"free": 100, "basic": 1000, "pro": 999_999}

# Prometheus counters (in-memory)
_metrics = {"requests_total": 0, "cache_hits": 0, "score_computed": 0, "rate_limited": 0}


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------

def _init_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS api_keys (
            api_key     TEXT PRIMARY KEY,
            tier        TEXT NOT NULL DEFAULT 'free',
            owner       TEXT,
            created_at  TEXT NOT NULL,
            is_active   INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rate_usage (
            api_key     TEXT NOT NULL,
            use_date    TEXT NOT NULL,
            req_count   INTEGER DEFAULT 0,
            PRIMARY KEY (api_key, use_date)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS score_cache (
            claim_hash  TEXT PRIMARY KEY,
            claim_text  TEXT NOT NULL,
            score       REAL NOT NULL,
            verdict     TEXT NOT NULL,
            evidence    TEXT NOT NULL,
            sources     TEXT NOT NULL,
            computed_at TEXT NOT NULL
        )
    """)
    conn.commit()
    # Ensure dev key exists
    _ensure_dev_key(conn)
    return conn


def _ensure_dev_key(conn) -> None:
    exists = conn.execute("SELECT 1 FROM api_keys WHERE api_key=?", (_DEV_KEY,)).fetchone()
    if not exists:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO api_keys (api_key, tier, owner, created_at) VALUES (?,?,?,?)",
            (_DEV_KEY, "pro", "dev", now),
        )
        conn.commit()


@contextmanager
def _tx(conn):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _get_key_info(conn, api_key: str) -> Optional[Dict]:
    row = conn.execute(
        "SELECT api_key, tier, owner, is_active FROM api_keys WHERE api_key=?", (api_key,)
    ).fetchone()
    return {"api_key": row[0], "tier": row[1], "owner": row[2], "active": bool(row[3])} if row else None


def _check_rate_limit(conn, api_key: str, tier: str) -> bool:
    today   = date.today().isoformat()
    limit   = _RATE_LIMITS.get(tier, 100)
    row     = conn.execute(
        "SELECT req_count FROM rate_usage WHERE api_key=? AND use_date=?", (api_key, today)
    ).fetchone()
    count   = row[0] if row else 0
    if count >= limit:
        return False
    with _tx(conn):
        conn.execute("""
            INSERT INTO rate_usage (api_key, use_date, req_count) VALUES (?,?,1)
            ON CONFLICT(api_key, use_date) DO UPDATE SET req_count=req_count+1
        """, (api_key, today))
    return True


def _get_cached_score(conn, claim_hash: str) -> Optional[Dict]:
    row = conn.execute(
        "SELECT score, verdict, evidence, sources, computed_at FROM score_cache WHERE claim_hash=?",
        (claim_hash,)
    ).fetchone()
    if row is None:
        return None
    return {
        "score": row[0], "verdict": row[1],
        "evidence_summary": row[2],
        "sources": json.loads(row[3]),
        "computed_at": row[4],
        "cached": True,
    }


def _cache_score(conn, claim_hash: str, claim_text: str, result: Dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _tx(conn):
        conn.execute("""
            INSERT INTO score_cache (claim_hash, claim_text, score, verdict, evidence, sources, computed_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(claim_hash) DO UPDATE SET
              score=excluded.score, verdict=excluded.verdict,
              evidence=excluded.evidence, sources=excluded.sources,
              computed_at=excluded.computed_at
        """, (
            claim_hash, claim_text[:500],
            result["score"], result["verdict"],
            result["evidence_summary"],
            json.dumps(result.get("sources", []), ensure_ascii=True),
            now,
        ))


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------

def _claim_hash(claim_text: str) -> str:
    import hashlib
    return hashlib.sha256(claim_text.strip().lower().encode("utf-8")).hexdigest()[:32]


def _lookup_historical(claim_text: str) -> Optional[Dict]:
    if not _FACT_CHECK_DB.exists():
        return None
    try:
        conn  = sqlite3.connect(str(_FACT_CHECK_DB))
        lower = claim_text.strip().lower()[:200]
        row   = conn.execute("""
            SELECT verdict, confidence, context_note, sources
            FROM claims
            WHERE lower(raw_text) LIKE ?
            ORDER BY detected_at DESC LIMIT 1
        """, (f"%{lower[:50]}%",)).fetchone()
        conn.close()
        if row:
            verdict, confidence, context_note, sources_raw = row
            return {
                "verdict": verdict,
                "score": float(confidence or 0),
                "evidence_summary": str(context_note or ""),
                "sources": json.loads(sources_raw) if sources_raw else [],
                "source": "historical",
            }
    except Exception:
        pass
    return None


def _score_with_claude(claim_text: str, context: str = "") -> Dict:
    try:
        import anthropic
        client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY", "local"),
            base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        )
        prompt = f"""You are a fact-checker. Evaluate this claim and respond with ONLY valid JSON.

Claim: {claim_text[:500]}
{"Context: " + context[:200] if context else ""}

Respond with exactly this JSON structure:
{{
  "verdict": "TRUE|FALSE|MISLEADING|UNVERIFIED|SATIRE",
  "score": <0.0-1.0 confidence>,
  "evidence_summary": "<1-2 sentence explanation>",
  "sources": ["<suggested source 1>", "<suggested source 2>"]
}}"""
        resp = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        # Extract JSON from response
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        data  = json.loads(raw[start:end])
        return {
            "verdict":          str(data.get("verdict", "UNVERIFIED")).upper(),
            "score":            float(data.get("score", 0.5)),
            "evidence_summary": str(data.get("evidence_summary", "")),
            "sources":          list(data.get("sources", [])),
            "source":           "claude",
        }
    except Exception as exc:
        _log.warning("trust_api_claude_error error=%s", exc)
        return _fallback_score(claim_text)


def _fallback_score(claim_text: str) -> Dict:
    text_lower = claim_text.lower()
    if any(w in text_lower for w in ["confirmed", "official", "study shows", "research"]):
        verdict, score = "UNVERIFIED", 0.4
    elif any(w in text_lower for w in ["breaking", "exposed", "secret", "they don't want"]):
        verdict, score = "MISLEADING", 0.3
    else:
        verdict, score = "UNVERIFIED", 0.5
    return {
        "verdict": verdict,
        "score": score,
        "evidence_summary": "Automated assessment — manual review recommended.",
        "sources": [],
        "source": "fallback",
    }


def compute_trust_score(conn, claim_text: str, context: str = "") -> Dict:
    claim_hash = _claim_hash(claim_text)

    # 1. Cache
    cached = _get_cached_score(conn, claim_hash)
    if cached:
        _metrics["cache_hits"] += 1
        return cached

    # 2. Historical DB
    historical = _lookup_historical(claim_text)
    if historical:
        _cache_score(conn, claim_hash, claim_text, historical)
        return {**historical, "cached": False}

    # 3. Claude
    result = _score_with_claude(claim_text, context)
    _cache_score(conn, claim_hash, claim_text, result)
    _metrics["score_computed"] += 1
    return {**result, "cached": False}


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

def _make_handler(conn: sqlite3.Connection):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)

            if parsed.path == "/health":
                self._json({"ok": True, "model": _CLAUDE_MODEL})
                return

            if parsed.path == "/metrics":
                lines = [f'trust_api_{k} {v}' for k, v in _metrics.items()]
                self._text("\n".join(lines) + "\n", "text/plain")
                return

            api_key = self.headers.get("X-API-Key", "")
            auth    = self._authenticate(api_key)
            if not auth:
                self._json({"error": "unauthorized"}, 401)
                return

            if parsed.path.startswith("/trust/claim/"):
                claim_id = parsed.path.removeprefix("/trust/claim/").strip("/")
                result   = self._lookup_claim(claim_id)
                self._json(result if result else {"error": "not_found"}, 200 if result else 404)
                return

            self._json({"error": "not_found"}, 404)

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path != "/trust/score":
                self._json({"error": "not_found"}, 404)
                return

            api_key = self.headers.get("X-API-Key", "")
            auth    = self._authenticate(api_key)
            if not auth:
                self._json({"error": "unauthorized"}, 401)
                return

            if not _check_rate_limit(conn, api_key, auth["tier"]):
                _metrics["rate_limited"] += 1
                self._json({"error": "rate_limit_exceeded", "tier": auth["tier"]}, 429)
                return

            try:
                cl   = int(self.headers.get("Content-Length", "0") or "0")
                body = json.loads(self.rfile.read(cl)) if cl else {}
            except Exception:
                self._json({"error": "invalid_json"}, 400)
                return

            claim_text = str(body.get("claim_text", "")).strip()
            context    = str(body.get("context", "")).strip()
            if not claim_text:
                self._json({"error": "claim_text is required"}, 400)
                return

            _metrics["requests_total"] += 1
            t0     = time.monotonic()
            result = compute_trust_score(conn, claim_text, context)
            ms     = int((time.monotonic() - t0) * 1000)

            self._json({
                "claim_text":       claim_text[:300],
                "verdict":          result["verdict"],
                "score":            result["score"],
                "evidence_summary": result["evidence_summary"],
                "sources":          result.get("sources", []),
                "cached":           result.get("cached", False),
                "source":           result.get("source", "unknown"),
                "latency_ms":       ms,
                "model":            _CLAUDE_MODEL,
            })

        def _authenticate(self, api_key: str) -> Optional[Dict]:
            if not api_key:
                return None
            return _get_key_info(conn, api_key)

        def _lookup_claim(self, claim_id: str) -> Optional[Dict]:
            if not _FACT_CHECK_DB.exists():
                return None
            try:
                db  = sqlite3.connect(str(_FACT_CHECK_DB))
                row = db.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
                db.close()
                if row:
                    cols = [d[0] for d in sqlite3.connect(str(_FACT_CHECK_DB))
                            .execute("SELECT * FROM claims LIMIT 0").description]
                    return dict(zip(cols, row))
            except Exception:
                pass
            return None

        def _json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=True).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _text(self, content: str, content_type: str):
            body = content.encode()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    conn   = _init_db(_DB_PATH)
    server = HTTPServer(("0.0.0.0", _PORT), _make_handler(conn))
    _log.info("trust_api_starting port=%d model=%s dev_key=%s", _PORT, _CLAUDE_MODEL, _DEV_KEY)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log.info("trust_api_shutting_down")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
