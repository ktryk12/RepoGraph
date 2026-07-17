"""
PostgreSQL Store for Editorial Agents Service

Provides database persistence for editorial operations,
content creation, review processes, and publishing workflows.
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


class PostgreSQLEditorialStore(BaseStore):
    """
    PostgreSQL persistence layer for editorial agent operations

    Handles:
    - Content creation and editorial workflows
    - Article drafts and publishing pipeline
    - Review processes and approval tracking
    - Editorial metrics and performance
    - Audience targeting and content strategy
    """

    def __init__(self, connection_pool: asyncpg.Pool):
        self.pool = connection_pool

    @classmethod
    async def create(cls, database_url: str) -> 'PostgreSQLEditorialStore':
        """Create store with connection pool"""
        try:
            pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
            logger.info("PostgreSQL connection pool created for editorial-agents")
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

    # Content Management
    async def create_content_item(self, content_data: Dict[str, Any]) -> str:
        """Create a new content item"""
        content_id = str(uuid.uuid4())

        async with self.get_connection() as conn:
            await conn.execute("""
                INSERT INTO editorial_content
                (content_id, content_type, title, status, content_data, created_by, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
                content_id,
                content_data.get('content_type'),
                content_data.get('title'),
                'draft',
                json.dumps(content_data),
                content_data.get('created_by'),
                datetime.utcnow()
            )

        logger.info(f"Created content item: {content_id}")
        return content_id

    async def update_content_status(self, content_id: str, status: str, reviewer: Optional[str] = None):
        """Update content status through editorial workflow"""
        async with self.get_connection() as conn:
            await conn.execute("""
                UPDATE editorial_content
                SET status = $1, reviewed_by = $2, updated_at = $3
                WHERE content_id = $4
            """,
                status,
                reviewer,
                datetime.utcnow(),
                content_id
            )

        logger.info(f"Updated content {content_id} status: {status}")

    async def get_content_by_status(self, status: str) -> List[Dict[str, Any]]:
        """Get content items by status"""
        async with self.get_connection() as conn:
            rows = await conn.fetch("""
                SELECT * FROM editorial_content
                WHERE status = $1
                ORDER BY created_at DESC
            """, status)

        return [dict(row) for row in rows]

    # Review Workflow
    async def create_review_task(self, review_data: Dict[str, Any]) -> str:
        """Create a review task for content"""
        review_id = str(uuid.uuid4())

        async with self.get_connection() as conn:
            await conn.execute("""
                INSERT INTO editorial_reviews
                (review_id, content_id, review_type, reviewer_type, review_criteria, status, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
                review_id,
                review_data.get('content_id'),
                review_data.get('review_type'),
                review_data.get('reviewer_type'),
                json.dumps(review_data.get('criteria', {})),
                'pending',
                datetime.utcnow()
            )

        logger.info(f"Created review task: {review_id}")
        return review_id

    async def submit_review(self, review_id: str, review_result: Dict[str, Any]):
        """Submit review results"""
        async with self.get_connection() as conn:
            await conn.execute("""
                UPDATE editorial_reviews
                SET status = $1, review_result = $2, completed_at = $3
                WHERE review_id = $4
            """,
                review_result.get('decision'),
                json.dumps(review_result),
                datetime.utcnow(),
                review_id
            )

        logger.info(f"Submitted review: {review_id}")

    # Publishing Pipeline
    async def schedule_publication(self, publication_data: Dict[str, Any]) -> str:
        """Schedule content for publication"""
        publication_id = str(uuid.uuid4())

        async with self.get_connection() as conn:
            await conn.execute("""
                INSERT INTO publication_schedule
                (publication_id, content_id, platform, scheduled_time, publication_data, status, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
                publication_id,
                publication_data.get('content_id'),
                publication_data.get('platform'),
                publication_data.get('scheduled_time'),
                json.dumps(publication_data),
                'scheduled',
                datetime.utcnow()
            )

        logger.info(f"Scheduled publication: {publication_id}")
        return publication_id

    # Editorial Metrics
    async def record_editorial_metrics(self, metrics_data: Dict[str, Any]):
        """Record editorial performance metrics"""
        async with self.get_connection() as conn:
            await conn.execute("""
                INSERT INTO editorial_metrics
                (agent_type, content_id, metric_type, metric_value, metrics_data, recorded_at)
                VALUES ($1, $2, $3, $4, $5, $6)
            """,
                metrics_data.get('agent_type'),
                metrics_data.get('content_id'),
                metrics_data.get('metric_type'),
                metrics_data.get('metric_value'),
                json.dumps(metrics_data),
                datetime.utcnow()
            )

        logger.info(f"Recorded editorial metrics for content: {metrics_data.get('content_id')}")

    async def get_editorial_analytics(self, start_date: Optional[datetime] = None,
                                    end_date: Optional[datetime] = None) -> Dict[str, Any]:
        """Get editorial analytics and performance data"""
        query = """
            SELECT
                metric_type,
                COUNT(*) as count,
                AVG(metric_value) as avg_value,
                MAX(metric_value) as max_value,
                MIN(metric_value) as min_value
            FROM editorial_metrics
            WHERE 1=1
        """
        params = []
        param_count = 1

        if start_date:
            query += f" AND recorded_at >= ${param_count}"
            params.append(start_date)
            param_count += 1

        if end_date:
            query += f" AND recorded_at <= ${param_count}"
            params.append(end_date)
            param_count += 1

        query += " GROUP BY metric_type ORDER BY metric_type"

        async with self.get_connection() as conn:
            rows = await conn.fetch(query, *params)

        return {row['metric_type']: dict(row) for row in rows}