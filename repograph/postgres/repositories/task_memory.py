"""Postgres-backed TaskMemory repository — same interface as memory/store.py."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from repograph.memory.models import (
    PatchRecord,
    PrecisionSignals,
    TaskMemoryRecord,
    TestFailureRecord,
)

from repograph.postgres.tracer import _get_conn


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class TaskMemoryRepository:
    """Thin wrapper — degrades gracefully to no-op if Postgres unavailable."""

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def create(
        self,
        query: str,
        task_family: str,
        working_set_id: str = "",
        retrieval_id: str = "",
        tenant_id: str = "default",
    ) -> TaskMemoryRecord:
        task_id = f"task:{uuid.uuid4()}"
        now = _now()
        record = TaskMemoryRecord(
            task_id=task_id,
            query=query,
            task_family=task_family,
            working_set_id=working_set_id,
            retrieval_id=retrieval_id,
            created_at=now,
            updated_at=now,
        )
        conn = _get_conn()
        if conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO task_memory
                            (task_id, tenant_id, query, task_family,
                             working_set_id, retrieval_id, status, flags,
                             created_at, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (task_id) DO NOTHING
                        """,
                        (task_id, tenant_id, query, task_family,
                         working_set_id, retrieval_id, "open", "{}",
                         now, now),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
        return record

    def get(self, task_id: str) -> TaskMemoryRecord | None:
        conn = _get_conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT query, task_family, working_set_id, retrieval_id, "
                    "status, flags, created_at, updated_at FROM task_memory "
                    "WHERE task_id = %s",
                    (task_id,),
                )
                row = cur.fetchone()
            if not row:
                return None
            query, family, ws_id, ret_id, status, flags, created, updated = row
            patches = self._get_patches(task_id)
            failures = self._get_failures(task_id)
            return TaskMemoryRecord(
                task_id=task_id,
                query=query or "",
                task_family=family or "",
                working_set_id=ws_id or "",
                retrieval_id=ret_id or "",
                status=status or "active",
                patches=patches,
                test_failures=failures,
                created_at=str(created),
                updated_at=str(updated),
            )
        except Exception:
            return None

    def update_signals(
        self, task_id: str, signals: PrecisionSignals
    ) -> TaskMemoryRecord | None:
        conn = _get_conn()
        if not conn:
            return None
        status = "completed" if (signals.verification_passed and signals.consumer_accepted) else "open"
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE task_memory SET status=%s, flags=%s, updated_at=%s "
                    "WHERE task_id=%s",
                    (status, json.dumps(signals.model_dump()), _now(), task_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
        return self.get(task_id)

    def add_patch(self, task_id: str, patch: PatchRecord, tenant_id: str = "default") -> TaskMemoryRecord | None:
        conn = _get_conn()
        if not conn:
            return None
        attempt = len(self._get_patches(task_id)) + 1
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO task_patches "
                    "(task_id, tenant_id, attempt, diff, result, failure_reason) "
                    "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                    (task_id, tenant_id, attempt,
                     patch.diff_summary,
                     patch.verification_result or "pending",
                     patch.failure_reason),
                )
                patch_row_id = cur.fetchone()[0]
                for sym in patch.symbols_touched:
                    cur.execute(
                        "INSERT INTO task_patch_symbols (patch_id, symbol) VALUES (%s,%s)",
                        (patch_row_id, sym),
                    )
                cur.execute(
                    "UPDATE task_memory SET updated_at=%s WHERE task_id=%s",
                    (_now(), task_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
        return self.get(task_id)

    def add_test_failure(self, task_id: str, failure: TestFailureRecord, tenant_id: str = "default") -> TaskMemoryRecord | None:
        conn = _get_conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO task_test_failures "
                    "(task_id, tenant_id, test_name, error_summary) "
                    "VALUES (%s,%s,%s,%s)",
                    (task_id, tenant_id,
                     failure.test_symbol if hasattr(failure, "test_symbol") else "",
                     failure.failure_message if hasattr(failure, "failure_message") else ""),
                )
                cur.execute(
                    "UPDATE task_memory SET updated_at=%s WHERE task_id=%s",
                    (_now(), task_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
        return self.get(task_id)

    def set_status(self, task_id: str, status: str) -> TaskMemoryRecord | None:
        conn = _get_conn()
        if not conn:
            return None
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE task_memory SET status=%s, updated_at=%s WHERE task_id=%s",
                    (status, _now(), task_id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
        return self.get(task_id)

    def list_recent(self, tenant_id: str = "default", limit: int = 20) -> list[TaskMemoryRecord]:
        conn = _get_conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT task_id FROM task_memory WHERE tenant_id=%s "
                    "ORDER BY updated_at DESC LIMIT %s",
                    (tenant_id, limit),
                )
                rows = cur.fetchall()
            return [r for task_id, in rows if (r := self.get(task_id))]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_patches(self, task_id: str) -> list[PatchRecord]:
        conn = _get_conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, created_at, diff, result, failure_reason FROM task_patches "
                    "WHERE task_id=%s ORDER BY attempt",
                    (task_id,),
                )
                rows = cur.fetchall()
            patches = []
            for row in rows:
                patch_row_id, created_at, diff, result, failure_reason = row
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT symbol FROM task_patch_symbols WHERE patch_id=%s",
                        (patch_row_id,),
                    )
                    symbols = [r[0] for r in cur.fetchall()]
                patches.append(PatchRecord(
                    patch_id=str(patch_row_id),
                    attempted_at=str(created_at),
                    diff_summary=diff or "",
                    symbols_touched=symbols,
                    verification_result=result,
                    failure_reason=failure_reason,
                ))
            return patches
        except Exception:
            return []

    def _get_failures(self, task_id: str) -> list[TestFailureRecord]:
        conn = _get_conn()
        if not conn:
            return []
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT test_name, error_summary, created_at FROM task_test_failures "
                    "WHERE task_id=%s ORDER BY created_at",
                    (task_id,),
                )
                rows = cur.fetchall()
            return [
                TestFailureRecord(
                    test_symbol=row[0] or "",
                    failure_message=row[1] or "",
                    recorded_at=str(row[2]),
                )
                for row in rows
            ]
        except Exception:
            return []
