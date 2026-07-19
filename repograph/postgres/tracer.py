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
    baseline_tokens: int = 0,
    saved_tokens_vs_baseline: int = 0,
    cache_hit: bool = False,
    cache_saved_tokens: int = 0,
    reused_tokens: int = 0,
    repo_revision: str | None = None,
    content_hash: str | None = None,
    session_id: str | None = None,
    task_hint: str | None = None,
    target_model: str | None = None,
    adapter_version: str | None = None,
    analysis_step_id: str | None = None,
    tokenizer_profile: str = "generic",
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
                    baseline_tokens, saved_tokens_vs_baseline, cache_hit,
                    cache_saved_tokens, reused_tokens, repo_revision, content_hash,
                    session_id, task_hint, target_model, adapter_version,
                    analysis_step_id, tokenizer_profile, extra, persisted_at
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s, NOW()
                )
                ON CONFLICT (retrieval_id) DO NOTHING
                """,
                (
                    retrieval_id, tenant_id, query[:500], task_family,
                    token_budget, token_estimate, duration_ms, consumer,
                    compressor_strategy, pre_compress_tokens, post_compress_tokens,
                    baseline_tokens, saved_tokens_vs_baseline, cache_hit,
                    cache_saved_tokens, reused_tokens, repo_revision, content_hash,
                    session_id, task_hint, target_model, adapter_version,
                    analysis_step_id, tokenizer_profile,
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
