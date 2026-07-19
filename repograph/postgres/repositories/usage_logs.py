"""Postgres-backed UsageLog repository."""
from __future__ import annotations

from repograph.postgres.metrics import UsageTotals
from repograph.postgres.tracer import _get_conn


class UsageRepository:

    def log(
        self,
        *,
        tenant_id: str,
        model_id: str,
        capability: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        task_id: str | None = None,
        repo_revision: str | None = None,
        content_hash: str | None = None,
        session_id: str | None = None,
        task_hint: str | None = None,
        target_model: str | None = None,
        adapter_version: str = "v1",
        analysis_step_id: str | None = None,
        tokenizer_profile: str = "generic",
        baseline_input_tokens: int = 0,
        repograph_input_tokens: int | None = None,
        cache_hit: bool = False,
        cache_saved_tokens: int = 0,
        reused_tokens: int = 0,
        input_price_usd: float = 0.0,
        output_price_usd: float = 0.0,
        verified_success: bool | None = None,
    ) -> None:
        conn = _get_conn()
        if not conn:
            return
        repograph_tokens = input_tokens if repograph_input_tokens is None else repograph_input_tokens
        saved_tokens = max(0, baseline_input_tokens - repograph_tokens)
        total_price = max(0.0, input_price_usd) + max(0.0, output_price_usd)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO usage_logs
                        (tenant_id, model_id, capability,
                         input_tokens, output_tokens, latency_ms, task_id,
                         repo_revision, content_hash, session_id, task_hint,
                         target_model, adapter_version, analysis_step_id,
                         tokenizer_profile, baseline_input_tokens,
                         repograph_input_tokens, saved_tokens_vs_baseline,
                         cache_hit, cache_saved_tokens, reused_tokens,
                         input_price_usd, output_price_usd, total_price_usd,
                         verified_success)
                    VALUES (
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s
                    )
                    """,
                    (
                        tenant_id, model_id, capability,
                        input_tokens, output_tokens, latency_ms, task_id,
                        repo_revision, content_hash, session_id, task_hint,
                        target_model or model_id, adapter_version, analysis_step_id,
                        tokenizer_profile, baseline_input_tokens, repograph_tokens,
                        saved_tokens, cache_hit, cache_saved_tokens, reused_tokens,
                        max(0.0, input_price_usd), max(0.0, output_price_usd),
                        total_price, verified_success,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()

    def mark_verified(self, task_id: str, passed: bool, tenant_id: str | None = None) -> None:
        conn = _get_conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                if tenant_id is None:
                    cur.execute(
                        "UPDATE usage_logs SET verified_success=%s WHERE task_id=%s",
                        (passed, task_id),
                    )
                else:
                    cur.execute(
                        "UPDATE usage_logs SET verified_success=%s WHERE tenant_id=%s AND task_id=%s",
                        (passed, tenant_id, task_id),
                    )
            conn.commit()
        except Exception:
            conn.rollback()

    def summary(self, tenant_id: str) -> dict:
        conn = _get_conn()
        if not conn:
            return {"available": False}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COALESCE(target_model, model_id), COUNT(*),
                        COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0),
                        AVG(latency_ms),
                        COUNT(DISTINCT task_id) FILTER (WHERE verified_success IS TRUE),
                        COALESCE(SUM(baseline_input_tokens), 0),
                        COALESCE(SUM(saved_tokens_vs_baseline), 0),
                        COALESCE(SUM(cache_saved_tokens), 0),
                        COALESCE(SUM(reused_tokens), 0),
                        COUNT(*) FILTER (WHERE cache_hit IS TRUE),
                        COALESCE(SUM(total_price_usd), 0)
                    FROM usage_logs
                    WHERE tenant_id=%s
                    GROUP BY COALESCE(target_model, model_id)
                    ORDER BY COUNT(*) DESC
                    """,
                    (tenant_id,),
                )
                rows = cur.fetchall()
                cur.execute(
                    """
                    SELECT
                        COUNT(*), COALESCE(SUM(input_tokens), 0),
                        COALESCE(SUM(output_tokens), 0),
                        COUNT(DISTINCT task_id) FILTER (WHERE verified_success IS TRUE),
                        COALESCE(SUM(baseline_input_tokens), 0),
                        COALESCE(SUM(saved_tokens_vs_baseline), 0),
                        COALESCE(SUM(cache_saved_tokens), 0),
                        COALESCE(SUM(reused_tokens), 0),
                        COUNT(*) FILTER (WHERE cache_hit IS TRUE),
                        COALESCE(SUM(total_price_usd), 0)
                    FROM usage_logs WHERE tenant_id=%s
                    """,
                    (tenant_id,),
                )
                totals = cur.fetchone()
            return {
                "tenant_id": tenant_id,
                "models": [
                    {
                        "model_id": row[0],
                        "avg_latency_ms": round(float(row[4] or 0), 1),
                        **_totals_from_model_row(row).as_metrics(),
                    }
                    for row in rows
                ],
                "metrics": _totals_from_summary_row(totals).as_metrics(),
            }
        except Exception:
            return {"available": False}


def _totals_from_model_row(row) -> UsageTotals:
    return UsageTotals(
        calls=int(row[1] or 0), input_tokens=int(row[2] or 0),
        output_tokens=int(row[3] or 0), verified_successes=int(row[5] or 0),
        baseline_input_tokens=int(row[6] or 0), saved_tokens_vs_baseline=int(row[7] or 0),
        cache_saved_tokens=int(row[8] or 0), reused_tokens=int(row[9] or 0),
        cache_hits=int(row[10] or 0), total_price_usd=float(row[11] or 0),
    )


def _totals_from_summary_row(row) -> UsageTotals:
    if not row:
        return UsageTotals()
    return UsageTotals(
        calls=int(row[0] or 0), input_tokens=int(row[1] or 0),
        output_tokens=int(row[2] or 0), verified_successes=int(row[3] or 0),
        baseline_input_tokens=int(row[4] or 0), saved_tokens_vs_baseline=int(row[5] or 0),
        cache_saved_tokens=int(row[6] or 0), reused_tokens=int(row[7] or 0),
        cache_hits=int(row[8] or 0), total_price_usd=float(row[9] or 0),
    )
