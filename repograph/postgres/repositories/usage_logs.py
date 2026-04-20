"""Postgres-backed UsageLog repository."""
from __future__ import annotations

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
    ) -> None:
        conn = _get_conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO usage_logs
                        (tenant_id, model_id, capability,
                         input_tokens, output_tokens, latency_ms, task_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (tenant_id, model_id, capability,
                     input_tokens, output_tokens, latency_ms, task_id),
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
                    "SELECT model_id, COUNT(*), SUM(input_tokens), SUM(output_tokens), AVG(latency_ms) "
                    "FROM usage_logs WHERE tenant_id=%s GROUP BY model_id ORDER BY COUNT(*) DESC",
                    (tenant_id,),
                )
                rows = cur.fetchall()
            return {
                "tenant_id": tenant_id,
                "models": [
                    {"model_id": r[0], "calls": r[1],
                     "input_tokens": r[2], "output_tokens": r[3],
                     "avg_latency_ms": round(float(r[4] or 0), 1)}
                    for r in rows
                ],
            }
        except Exception:
            return {"available": False}
