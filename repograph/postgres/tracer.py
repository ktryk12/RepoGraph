"""Postgres trace logger — optional, degrades gracefully uden psycopg2."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

LOGGER = logging.getLogger(__name__)

POSTGRES_DSN = os.getenv("REPOGRAPH_POSTGRES_DSN", "")

_conn = None
_available: bool | None = None


def _get_conn():
    global _conn, _available
    if _available is False:
        return None
    if _conn is not None:
        try:
            _conn.cursor().execute("SELECT 1")
            return _conn
        except Exception:
            _conn = None
    if not POSTGRES_DSN:
        _available = False
        return None
    try:
        import psycopg2
        _conn = psycopg2.connect(POSTGRES_DSN)
        _conn.autocommit = True
        _available = True
        LOGGER.info("Postgres connected: %s", POSTGRES_DSN[:30])
    except Exception as exc:
        LOGGER.warning("Postgres unavailable (%s) — traces skipped", exc)
        _available = False
        _conn = None
    return _conn


def is_available() -> bool:
    return _get_conn() is not None


def log_retrieval_trace(
    *,
    retrieval_id: str,
    tenant_id: str,
    query: str,
    task_family: str,
    token_budget: int,
    token_estimate: int,
    duration_ms: int,
    consumer: str = "generic",
    compressor_strategy: str = "none",
    pre_compress_tokens: int = 0,
    post_compress_tokens: int = 0,
    extra: dict[str, Any] | None = None,
) -> None:
    conn = _get_conn()
    if not conn:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO retrieval_traces (
                    retrieval_id, tenant_id, query, task_family,
                    token_budget, token_estimate, duration_ms, consumer,
                    compressor_strategy, pre_compress_tokens, post_compress_tokens,
                    extra, persisted_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
                ON CONFLICT (retrieval_id) DO NOTHING
                """,
                (
                    retrieval_id, tenant_id, query[:500], task_family,
                    token_budget, token_estimate, duration_ms, consumer,
                    compressor_strategy, pre_compress_tokens, post_compress_tokens,
                    json.dumps(extra or {}),
                ),
            )
    except Exception as exc:
        LOGGER.debug("Trace insert failed: %s", exc)


def status() -> dict[str, Any]:
    conn = _get_conn()
    if not conn:
        return {"available": False, "dsn_set": bool(POSTGRES_DSN)}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version()")
            version = cur.fetchone()[0]
        return {"available": True, "version": version}
    except Exception as exc:
        return {"available": False, "error": str(exc)}
