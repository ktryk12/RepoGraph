"""
PostgreSQL Store for Verification Agents Service

Provides persistence for fact-checking, evidence gathering, and verification operations.
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


class PostgreSQLVerificationStore(BaseStore):
    """PostgreSQL persistence layer for verification agent operations"""

    def __init__(self, connection_pool: asyncpg.Pool):
        self.pool = connection_pool

    @classmethod
    async def create(cls, database_url: str) -> 'PostgreSQLVerificationStore':
        """Create store with connection pool"""
        try:
            pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
            logger.info("PostgreSQL connection pool created for verification-agents")
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

    # Service-specific methods would be implemented here
    async def create_task(self, task_data: Dict[str, Any]) -> str:
        """Create a new verification task"""
        task_id = str(uuid.uuid4())
        async with self.get_connection() as conn:
            await conn.execute("""
                INSERT INTO verification_tasks
                (task_id, task_type, task_data, status, created_at)
                VALUES ($1, $2, $3, $4, $5)
            """,
                task_id,
                task_data.get('task_type'),
                json.dumps(task_data),
                'pending',
                datetime.utcnow()
            )
        return task_id

    async def get_tasks(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get tasks with optional status filter"""
        query = "SELECT * FROM verification_tasks"
        params = []
        if status:
            query += " WHERE status = $1"
            params.append(status)
        query += " ORDER BY created_at DESC"
        
        async with self.get_connection() as conn:
            rows = await conn.fetch(query, *params)
        return [dict(row) for row in rows]
