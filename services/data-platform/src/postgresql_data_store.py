"""
PostgreSQL Data Store

Unified database persistence for consolidated data platform:
- Data export jobs and format management
- Artifact storage and validation metadata
- Execution audit trails and immutable records
- Content publishing operations and platform tracking
"""

import asyncio
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from uuid import uuid4

import asyncpg

logger = logging.getLogger(__name__)


class PostgreSQLDataStore:
    """PostgreSQL store for data platform operations"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[asyncpg.Pool] = None

    @classmethod
    async def create(cls, database_url: str) -> "PostgreSQLDataStore":
        """Create and initialize data store"""
        store = cls(database_url)
        await store.initialize()
        return store

    async def initialize(self) -> None:
        """Initialize database connection and tables"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=1,
                max_size=10,
                command_timeout=60
            )

            await self._ensure_tables()
            logger.info("Data store initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize data store: {e}")
            raise

    async def _ensure_tables(self) -> None:
        """Ensure all data platform tables exist"""
        async with self.pool.acquire() as conn:
            # Data export jobs
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS export_jobs (
                    job_id VARCHAR PRIMARY KEY,
                    export_type VARCHAR NOT NULL, -- 'claims', 'trades', 'audit'
                    format VARCHAR NOT NULL, -- 'jsonld', 'csv', 'ndjson'
                    date_from DATE,
                    date_to DATE,
                    output_path VARCHAR,
                    status VARCHAR NOT NULL DEFAULT 'pending',
                    records_exported INTEGER DEFAULT 0,
                    file_size_bytes BIGINT DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    completed_at TIMESTAMPTZ,
                    metadata JSONB DEFAULT '{}'::jsonb
                )
            """)

            # Artifact records
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id VARCHAR PRIMARY KEY,
                    artifact_type VARCHAR NOT NULL, -- 'tool_evidence', 'manifest', 'report'
                    file_path VARCHAR NOT NULL,
                    file_hash VARCHAR, -- SHA256 hash for integrity
                    file_size_bytes BIGINT,
                    content_type VARCHAR,
                    validation_status VARCHAR NOT NULL DEFAULT 'pending',
                    validation_result JSONB,
                    created_by VARCHAR,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    metadata JSONB DEFAULT '{}'::jsonb
                )
            """)

            # Execution audit records (immutable)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS execution_audit_records (
                    audit_id VARCHAR PRIMARY KEY,
                    event_type VARCHAR NOT NULL, -- 'order.intent', 'order.executed', etc.
                    event_data JSONB NOT NULL,
                    kafka_topic VARCHAR,
                    kafka_partition INTEGER,
                    kafka_offset BIGINT,
                    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    -- Immutable: no updates allowed, only inserts
                    metadata JSONB DEFAULT '{}'::jsonb
                )
            """)

            # Publishing operations
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS publishing_operations (
                    operation_id VARCHAR PRIMARY KEY,
                    content_id VARCHAR NOT NULL,
                    platform VARCHAR NOT NULL, -- 'twitter', 'youtube', 'linkedin', 'tiktok', 'newsletter'
                    content_data JSONB NOT NULL,
                    publish_status VARCHAR NOT NULL DEFAULT 'pending',
                    platform_ref VARCHAR, -- Platform-specific ID after publishing
                    platform_response JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    published_at TIMESTAMPTZ,
                    metadata JSONB DEFAULT '{}'::jsonb
                )
            """)

            # Data export configurations
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS export_configurations (
                    config_id VARCHAR PRIMARY KEY,
                    export_name VARCHAR NOT NULL,
                    export_type VARCHAR NOT NULL,
                    format VARCHAR NOT NULL,
                    schedule_cron VARCHAR,
                    enabled BOOLEAN NOT NULL DEFAULT true,
                    last_export_at TIMESTAMPTZ,
                    config_data JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            # Performance metrics
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS data_performance_metrics (
                    metric_id VARCHAR PRIMARY KEY,
                    resource_type VARCHAR NOT NULL,
                    resource_id VARCHAR NOT NULL,
                    metric_type VARCHAR NOT NULL,
                    metric_value FLOAT NOT NULL,
                    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    metadata JSONB DEFAULT '{}'::jsonb
                )
            """)

            # Runtime configuration
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS data_runtime_config (
                    config_id VARCHAR PRIMARY KEY,
                    config_type VARCHAR NOT NULL,
                    config_data JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

    # Data Export Operations
    async def create_export_job(self, job_id: str, export_type: str, format: str,
                               date_from: Optional[str] = None, date_to: Optional[str] = None,
                               metadata: Optional[Dict] = None) -> None:
        """Create a new data export job"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO export_jobs (job_id, export_type, format, date_from, date_to, metadata)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, job_id, export_type, format, date_from, date_to, metadata or {})

    async def update_export_job(self, job_id: str, **updates) -> None:
        """Update export job status and data"""
        set_clauses = []
        values = []
        param_count = 1

        for key, value in updates.items():
            if key in ['status', 'output_path', 'records_exported', 'file_size_bytes', 'completed_at', 'metadata']:
                set_clauses.append(f"{key} = ${param_count}")
                values.append(value)
                param_count += 1

        values.append(job_id)
        query = f"UPDATE export_jobs SET {', '.join(set_clauses)} WHERE job_id = ${param_count}"

        async with self.pool.acquire() as conn:
            await conn.execute(query, *values)

    async def get_export_job(self, job_id: str) -> Optional[Dict]:
        """Get export job by ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM export_jobs WHERE job_id = $1", job_id)
            return dict(row) if row else None

    async def list_export_jobs(self, export_type: Optional[str] = None,
                              status: Optional[str] = None, limit: int = 100) -> List[Dict]:
        """List export jobs"""
        async with self.pool.acquire() as conn:
            query = "SELECT * FROM export_jobs WHERE 1=1"
            params = []
            param_count = 1

            if export_type:
                query += f" AND export_type = ${param_count}"
                params.append(export_type)
                param_count += 1

            if status:
                query += f" AND status = ${param_count}"
                params.append(status)
                param_count += 1

            query += f" ORDER BY created_at DESC LIMIT ${param_count}"
            params.append(limit)

            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    # Artifact Operations
    async def create_artifact(self, artifact_id: str, artifact_type: str, file_path: str,
                             **kwargs) -> None:
        """Create artifact record"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO artifacts (
                    artifact_id, artifact_type, file_path, file_hash,
                    file_size_bytes, content_type, validation_status,
                    validation_result, created_by, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
                artifact_id, artifact_type, file_path,
                kwargs.get('file_hash'),
                kwargs.get('file_size_bytes'),
                kwargs.get('content_type'),
                kwargs.get('validation_status', 'pending'),
                kwargs.get('validation_result', {}),
                kwargs.get('created_by'),
                kwargs.get('metadata', {})
            )

    async def update_artifact(self, artifact_id: str, **updates) -> None:
        """Update artifact record"""
        set_clauses = []
        values = []
        param_count = 1

        for key, value in updates.items():
            if key in ['validation_status', 'validation_result', 'metadata']:
                set_clauses.append(f"{key} = ${param_count}")
                values.append(value)
                param_count += 1

        values.append(artifact_id)
        query = f"UPDATE artifacts SET {', '.join(set_clauses)} WHERE artifact_id = ${param_count}"

        async with self.pool.acquire() as conn:
            await conn.execute(query, *values)

    async def get_artifact(self, artifact_id: str) -> Optional[Dict]:
        """Get artifact by ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM artifacts WHERE artifact_id = $1", artifact_id)
            return dict(row) if row else None

    # Execution Audit Operations (Immutable)
    async def create_audit_record(self, audit_id: str, event_type: str, event_data: Dict,
                                 kafka_topic: Optional[str] = None, kafka_partition: Optional[int] = None,
                                 kafka_offset: Optional[int] = None, metadata: Optional[Dict] = None) -> None:
        """Create immutable audit record"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO execution_audit_records (
                    audit_id, event_type, event_data, kafka_topic,
                    kafka_partition, kafka_offset, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """, audit_id, event_type, event_data, kafka_topic, kafka_partition, kafka_offset, metadata or {})

    async def get_audit_records(self, event_type: Optional[str] = None,
                               from_date: Optional[datetime] = None,
                               to_date: Optional[datetime] = None,
                               limit: int = 1000) -> List[Dict]:
        """Get audit records (read-only)"""
        async with self.pool.acquire() as conn:
            query = "SELECT * FROM execution_audit_records WHERE 1=1"
            params = []
            param_count = 1

            if event_type:
                query += f" AND event_type = ${param_count}"
                params.append(event_type)
                param_count += 1

            if from_date:
                query += f" AND recorded_at >= ${param_count}"
                params.append(from_date)
                param_count += 1

            if to_date:
                query += f" AND recorded_at <= ${param_count}"
                params.append(to_date)
                param_count += 1

            query += f" ORDER BY recorded_at DESC LIMIT ${param_count}"
            params.append(limit)

            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    # Publishing Operations
    async def create_publishing_operation(self, operation_id: str, content_id: str, platform: str,
                                         content_data: Dict, metadata: Optional[Dict] = None) -> None:
        """Create publishing operation"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO publishing_operations (operation_id, content_id, platform, content_data, metadata)
                VALUES ($1, $2, $3, $4, $5)
            """, operation_id, content_id, platform, content_data, metadata or {})

    async def update_publishing_operation(self, operation_id: str, **updates) -> None:
        """Update publishing operation"""
        set_clauses = []
        values = []
        param_count = 1

        for key, value in updates.items():
            if key in ['publish_status', 'platform_ref', 'platform_response', 'published_at', 'metadata']:
                set_clauses.append(f"{key} = ${param_count}")
                values.append(value)
                param_count += 1

        values.append(operation_id)
        query = f"UPDATE publishing_operations SET {', '.join(set_clauses)} WHERE operation_id = ${param_count}"

        async with self.pool.acquire() as conn:
            await conn.execute(query, *values)

    async def get_publishing_operation(self, operation_id: str) -> Optional[Dict]:
        """Get publishing operation by ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM publishing_operations WHERE operation_id = $1", operation_id)
            return dict(row) if row else None

    async def list_publishing_operations(self, platform: Optional[str] = None,
                                        status: Optional[str] = None,
                                        limit: int = 100) -> List[Dict]:
        """List publishing operations"""
        async with self.pool.acquire() as conn:
            query = "SELECT * FROM publishing_operations WHERE 1=1"
            params = []
            param_count = 1

            if platform:
                query += f" AND platform = ${param_count}"
                params.append(platform)
                param_count += 1

            if status:
                query += f" AND publish_status = ${param_count}"
                params.append(status)
                param_count += 1

            query += f" ORDER BY created_at DESC LIMIT ${param_count}"
            params.append(limit)

            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    # Export Configurations
    async def create_export_configuration(self, config_id: str, export_name: str,
                                         export_type: str, format: str, config_data: Dict,
                                         schedule_cron: Optional[str] = None) -> None:
        """Create export configuration"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO export_configurations
                (config_id, export_name, export_type, format, schedule_cron, config_data)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, config_id, export_name, export_type, format, schedule_cron, config_data)

    async def get_export_configuration(self, config_id: str) -> Optional[Dict]:
        """Get export configuration"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM export_configurations WHERE config_id = $1", config_id)
            return dict(row) if row else None

    # Performance Metrics
    async def record_performance_metric(self, metric_id: str, resource_type: str,
                                      resource_id: str, metric_type: str,
                                      metric_value: float, metadata: Optional[Dict] = None) -> None:
        """Record performance metric"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO data_performance_metrics
                (metric_id, resource_type, resource_id, metric_type, metric_value, metadata)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, metric_id, resource_type, resource_id, metric_type, metric_value, metadata or {})

    async def get_performance_metrics(self, resource_type: str, resource_id: str) -> List[Dict]:
        """Get performance metrics for resource"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM data_performance_metrics
                WHERE resource_type = $1 AND resource_id = $2
                ORDER BY recorded_at DESC
            """, resource_type, resource_id)
            return [dict(row) for row in rows]

    # Runtime Configuration
    async def create_runtime_config(self, config_id: str, config_type: str, config_data: Dict) -> None:
        """Create or update runtime configuration"""
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO data_runtime_config (config_id, config_type, config_data, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (config_id)
                DO UPDATE SET config_data = $3, updated_at = NOW()
            """, config_id, config_type, config_data)

    async def get_runtime_config(self, config_id: str) -> Optional[Dict]:
        """Get runtime configuration"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM data_runtime_config WHERE config_id = $1", config_id)
            return dict(row) if row else None

    async def close(self) -> None:
        """Close database connections"""
        if self.pool:
            await self.pool.close()
            logger.info("Data store connections closed")