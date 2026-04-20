"""Postgres-backed VerifierRun repository."""
from __future__ import annotations

import json

from repograph.postgres.tracer import _get_conn


class VerifierRunRepository:

    def log(
        self,
        *,
        tenant_id: str,
        task_id: str | None,
        repo_path: str,
        steps: list[str],
        passed: bool,
        result_json: dict,
        duration_ms: int,
    ) -> None:
        conn = _get_conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO verifier_runs
                        (tenant_id, task_id, repo_path, steps, passed,
                         result_json, duration_ms)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (tenant_id, task_id, repo_path,
                     steps, passed,
                     json.dumps(result_json), duration_ms),
                )
            conn.commit()
        except Exception:
            conn.rollback()

    def list_recent(self, tenant_id: str, limit: int = 20) -> list[dict]:
        conn = _get_conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT task_id, repo_path, steps, passed, duration_ms, created_at "
                    "FROM verifier_runs WHERE tenant_id=%s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (tenant_id, limit),
                )
                cols = ["task_id", "repo_path", "steps", "passed", "duration_ms", "created_at"]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            return []
