"""
PostgreSQL store for orchestrator-worker service

Provides persistence for workflow execution state, episode tracking, and worker results.
Follows the database-per-service pattern established in context-plane and memory-plane.
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import asyncpg

logger = logging.getLogger(__name__)


class PostgreSQLOrchestratorStore:
    """PostgreSQL implementation for orchestrator workflow persistence"""

    def __init__(self, connection_pool: asyncpg.Pool):
        self.pool = connection_pool

    @classmethod
    async def create(cls, database_url: str) -> "PostgreSQLOrchestratorStore":
        """Create store with connection pool"""
        pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
        return cls(pool)

    async def close(self) -> None:
        """Close connection pool"""
        await self.pool.close()

    # ── Episode Management ────────────────────────────────────────────────

    async def save_episode(
        self,
        episode_id: str,
        workflow_id: str,
        status: str,
        task_ref: str,
        truth_pack_ref: str,
        context_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        execution_result: Optional[Dict[str, Any]] = None,
        final_score: Optional[float] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Save or update episode record"""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO episodes (
                    episode_id, workflow_id, status, task_ref, truth_pack_ref,
                    context_id, metadata_json, created_at, updated_at,
                    execution_result, final_score, error_message
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                ON CONFLICT (episode_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    execution_result = EXCLUDED.execution_result,
                    final_score = EXCLUDED.final_score,
                    error_message = EXCLUDED.error_message,
                    updated_at = EXCLUDED.updated_at
                """,
                episode_id,
                workflow_id,
                status,
                task_ref,
                truth_pack_ref,
                context_id,
                json.dumps(metadata) if metadata else None,
                datetime.utcnow(),
                datetime.utcnow(),
                json.dumps(execution_result) if execution_result else None,
                final_score,
                error_message,
            )

    async def get_episode(self, episode_id: str) -> Optional[Dict[str, Any]]:
        """Get episode by ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT episode_id, workflow_id, status, task_ref, truth_pack_ref,
                       context_id, metadata_json, created_at, updated_at,
                       execution_result, final_score, error_message
                FROM episodes WHERE episode_id = $1
                """,
                episode_id,
            )

            if not row:
                return None

            return {
                "episode_id": row["episode_id"],
                "workflow_id": row["workflow_id"],
                "status": row["status"],
                "task_ref": row["task_ref"],
                "truth_pack_ref": row["truth_pack_ref"],
                "context_id": row["context_id"],
                "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "execution_result": json.loads(row["execution_result"]) if row["execution_result"] else None,
                "final_score": row["final_score"],
                "error_message": row["error_message"],
            }

    # ── Workflow State Management ──────────────────────────────────────────

    async def save_workflow_state(
        self,
        workflow_id: str,
        episode_id: str,
        current_node: Optional[str],
        completed_nodes: List[str],
        state_data: Dict[str, Any],
        status: str,
    ) -> None:
        """Save workflow execution state"""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO workflow_states (
                    workflow_id, episode_id, current_node, completed_nodes,
                    state_data, status, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (workflow_id) DO UPDATE SET
                    current_node = EXCLUDED.current_node,
                    completed_nodes = EXCLUDED.completed_nodes,
                    state_data = EXCLUDED.state_data,
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at
                """,
                workflow_id,
                episode_id,
                current_node,
                json.dumps(completed_nodes),
                json.dumps(state_data),
                status,
                datetime.utcnow(),
                datetime.utcnow(),
            )

    async def get_workflow_state(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Get current workflow state"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT workflow_id, episode_id, current_node, completed_nodes,
                       state_data, status, created_at, updated_at
                FROM workflow_states WHERE workflow_id = $1
                """,
                workflow_id,
            )

            if not row:
                return None

            return {
                "workflow_id": row["workflow_id"],
                "episode_id": row["episode_id"],
                "current_node": row["current_node"],
                "completed_nodes": json.loads(row["completed_nodes"]) if row["completed_nodes"] else [],
                "state_data": json.loads(row["state_data"]) if row["state_data"] else {},
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    # ── Worker Results Management ──────────────────────────────────────────

    async def save_worker_result(
        self,
        result_id: str,
        workflow_id: str,
        episode_id: str,
        worker_type: str,
        partition_id: str,
        result_data: Dict[str, Any],
        execution_time_ms: int,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """Save worker execution result"""
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO worker_results (
                    result_id, workflow_id, episode_id, worker_type, partition_id,
                    result_data, execution_time_ms, status, created_at, error_message
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                result_id,
                workflow_id,
                episode_id,
                worker_type,
                partition_id,
                json.dumps(result_data),
                execution_time_ms,
                status,
                datetime.utcnow(),
                error_message,
            )

    async def get_worker_results(self, workflow_id: str) -> List[Dict[str, Any]]:
        """Get worker results for workflow"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT result_id, workflow_id, episode_id, worker_type, partition_id,
                       result_data, execution_time_ms, status, created_at, error_message
                FROM worker_results
                WHERE workflow_id = $1
                ORDER BY created_at
                """,
                workflow_id,
            )

            return [
                {
                    "result_id": row["result_id"],
                    "workflow_id": row["workflow_id"],
                    "episode_id": row["episode_id"],
                    "worker_type": row["worker_type"],
                    "partition_id": row["partition_id"],
                    "result_data": json.loads(row["result_data"]) if row["result_data"] else {},
                    "execution_time_ms": row["execution_time_ms"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "error_message": row["error_message"],
                }
                for row in rows
            ]

    # ── Health and Maintenance ──────────────────────────────────────────────

    async def health_check(self) -> bool:
        """Verify database connectivity"""
        try:
            async with self.pool.acquire() as conn:
                result = await conn.fetchval("SELECT 1")
                return result == 1
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

    async def cleanup_old_records(self, older_than_days: int = 30) -> Dict[str, int]:
        """Clean up old records"""
        cutoff_date = datetime.utcnow() - timedelta(days=older_than_days)

        async with self.pool.acquire() as conn:
            # Delete old workflow states
            workflow_count = await conn.fetchval(
                "DELETE FROM workflow_states WHERE created_at < $1 AND status IN ('completed', 'failed')",
                cutoff_date,
            )

            # Delete old worker results
            worker_count = await conn.fetchval(
                "DELETE FROM worker_results WHERE created_at < $1", cutoff_date
            )

            # Delete old episodes
            episode_count = await conn.fetchval(
                "DELETE FROM episodes WHERE created_at < $1 AND status IN ('completed', 'failed')",
                cutoff_date,
            )

            return {
                "episodes_deleted": episode_count or 0,
                "workflow_states_deleted": workflow_count or 0,
                "worker_results_deleted": worker_count or 0,
            }