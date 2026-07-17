"""
PostgreSQL Store for Development Agents Service

Provides database persistence for development agent operations,
agent tasks, results, and development lifecycle data.
"""

import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager
import uuid

import asyncpg
from babyai_shared.storage.base_store import BaseStore

logger = logging.getLogger(__name__)


class PostgreSQLDevStore(BaseStore):
    """
    PostgreSQL persistence layer for development agent operations

    Handles:
    - Agent task tracking and history
    - Development artifacts and results
    - Code analysis and metrics
    - Architecture decisions and documentation
    - Performance tracking
    """

    def __init__(self, connection_pool: asyncpg.Pool):
        self.pool = connection_pool

    @classmethod
    async def create(cls, database_url: str) -> 'PostgreSQLDevStore':
        """Create store with connection pool"""
        try:
            pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
            logger.info("PostgreSQL connection pool created for development-agents")
            return cls(pool)
        except Exception as e:
            logger.error(f"Failed to create PostgreSQL pool: {e}")
            raise

    async def close(self):
        """Close connection pool"""
        if hasattr(self, 'pool') and self.pool:
            await self.pool.close()
            logger.info("PostgreSQL connection pool closed")

    @asynccontextmanager
    async def get_connection(self):
        """Get database connection from pool"""
        conn = await self.pool.acquire()
        try:
            yield conn
        finally:
            await self.pool.release(conn)

    # Agent Task Management
    async def create_agent_task(self, task_data: Dict[str, Any]) -> str:
        """Create a new development agent task"""
        task_id = str(uuid.uuid4())

        async with self.get_connection() as conn:
            await conn.execute("""
                INSERT INTO dev_agent_tasks
                (task_id, agent_type, task_type, task_description, task_data, status, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
                task_id,
                task_data.get('agent_type'),
                task_data.get('task_type'),
                task_data.get('description'),
                json.dumps(task_data),
                'pending',
                datetime.utcnow()
            )

        logger.info(f"Created development task: {task_id}")
        return task_id

    async def update_task_status(self, task_id: str, status: str, result_data: Optional[Dict[str, Any]] = None):
        """Update task status and results"""
        async with self.get_connection() as conn:
            await conn.execute("""
                UPDATE dev_agent_tasks
                SET status = $1, result_data = $2, updated_at = $3
                WHERE task_id = $4
            """,
                status,
                json.dumps(result_data) if result_data else None,
                datetime.utcnow(),
                task_id
            )

        logger.info(f"Updated task {task_id} status: {status}")

    async def get_agent_tasks(self, agent_type: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get agent tasks with optional filtering"""
        query = "SELECT * FROM dev_agent_tasks WHERE 1=1"
        params = []
        param_count = 1

        if agent_type:
            query += f" AND agent_type = ${param_count}"
            params.append(agent_type)
            param_count += 1

        if status:
            query += f" AND status = ${param_count}"
            params.append(status)
            param_count += 1

        query += " ORDER BY created_at DESC"

        async with self.get_connection() as conn:
            rows = await conn.fetch(query, *params)

        return [dict(row) for row in rows]

    # Development Artifacts
    async def store_artifact(self, artifact_data: Dict[str, Any]) -> str:
        """Store development artifact (code, docs, etc.)"""
        artifact_id = str(uuid.uuid4())

        async with self.get_connection() as conn:
            await conn.execute("""
                INSERT INTO dev_artifacts
                (artifact_id, task_id, artifact_type, content, metadata, created_at)
                VALUES ($1, $2, $3, $4, $5, $6)
            """,
                artifact_id,
                artifact_data.get('task_id'),
                artifact_data.get('artifact_type'),
                artifact_data.get('content'),
                json.dumps(artifact_data.get('metadata', {})),
                datetime.utcnow()
            )

        logger.info(f"Stored development artifact: {artifact_id}")
        return artifact_id

    async def get_artifacts_by_task(self, task_id: str) -> List[Dict[str, Any]]:
        """Get all artifacts for a specific task"""
        async with self.get_connection() as conn:
            rows = await conn.fetch("""
                SELECT * FROM dev_artifacts
                WHERE task_id = $1
                ORDER BY created_at DESC
            """, task_id)

        return [dict(row) for row in rows]

    # Agent Performance Tracking
    async def record_agent_metrics(self, metrics_data: Dict[str, Any]):
        """Record agent performance metrics"""
        async with self.get_connection() as conn:
            await conn.execute("""
                INSERT INTO agent_metrics
                (agent_id, agent_type, metrics_data, recorded_at)
                VALUES ($1, $2, $3, $4)
            """,
                metrics_data.get('agent_id'),
                metrics_data.get('agent_type'),
                json.dumps(metrics_data),
                datetime.utcnow()
            )

        logger.info(f"Recorded metrics for agent: {metrics_data.get('agent_id')}")

    async def get_agent_metrics(self, agent_type: Optional[str] = None,
                               start_date: Optional[datetime] = None,
                               end_date: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Get agent performance metrics with optional filtering"""
        query = "SELECT * FROM agent_metrics WHERE 1=1"
        params = []
        param_count = 1

        if agent_type:
            query += f" AND agent_type = ${param_count}"
            params.append(agent_type)
            param_count += 1

        if start_date:
            query += f" AND recorded_at >= ${param_count}"
            params.append(start_date)
            param_count += 1

        if end_date:
            query += f" AND recorded_at <= ${param_count}"
            params.append(end_date)
            param_count += 1

        query += " ORDER BY recorded_at DESC"

        async with self.get_connection() as conn:
            rows = await conn.fetch(query, *params)

        return [dict(row) for row in rows]