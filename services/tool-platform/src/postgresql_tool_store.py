"""
PostgreSQL Store for Tool Platform Service

Provides database persistence for tools, skills, executions,
and runtime state following the database-per-service pattern.
"""

import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

import asyncpg
from babyai_shared.storage.base_store import BaseStore

logger = logging.getLogger(__name__)


class PostgreSQLToolStore(BaseStore):
    """
    PostgreSQL persistence layer for tool platform operations

    Handles:
    - Tool definitions and metadata
    - Skill definitions and manifests
    - Tool and skill executions
    - Runtime configurations
    - Performance metrics
    """

    def __init__(self, connection_pool: asyncpg.Pool):
        self.pool = connection_pool

    @classmethod
    async def create(cls, database_url: str) -> 'PostgreSQLToolStore':
        """Create store with connection pool"""
        try:
            pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
            store = cls(pool)
            await store.initialize_schema()
            logger.info("PostgreSQL tool store initialized")
            return store
        except Exception as e:
            logger.error(f"Failed to create PostgreSQL tool store: {e}")
            raise

    async def initialize_schema(self) -> None:
        """Initialize database schema if not exists"""
        schema_sql = """
        -- Tool definitions table
        CREATE TABLE IF NOT EXISTS tool_definitions (
            tool_id VARCHAR(100) PRIMARY KEY,
            tool_name VARCHAR(200) NOT NULL,
            tool_type VARCHAR(50) NOT NULL,
            tool_spec JSON NOT NULL,
            version VARCHAR(20) DEFAULT '1.0',
            enabled BOOLEAN DEFAULT TRUE,
            metadata_json JSON,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        -- Skill definitions table
        CREATE TABLE IF NOT EXISTS skill_definitions (
            skill_id VARCHAR(100) PRIMARY KEY,
            skill_name VARCHAR(200) NOT NULL,
            skill_type VARCHAR(50) NOT NULL,
            skill_manifest JSON NOT NULL,
            version VARCHAR(20) DEFAULT '1.0',
            enabled BOOLEAN DEFAULT TRUE,
            dependencies JSON, -- Array of required tools
            metadata_json JSON,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        -- Tool executions table
        CREATE TABLE IF NOT EXISTS tool_executions (
            execution_id VARCHAR(100) PRIMARY KEY,
            tool_id VARCHAR(100) REFERENCES tool_definitions(tool_id),
            execution_context JSON,
            input_data JSON,
            output_data JSON,
            execution_state VARCHAR(20) DEFAULT 'pending',
            started_at TIMESTAMP WITH TIME ZONE,
            completed_at TIMESTAMP WITH TIME ZONE,
            duration_ms INTEGER,
            error_data JSON,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        -- Skill executions table
        CREATE TABLE IF NOT EXISTS skill_executions (
            execution_id VARCHAR(100) PRIMARY KEY,
            skill_id VARCHAR(100) REFERENCES skill_definitions(skill_id),
            context_pack JSON,
            input_data JSON,
            output_data JSON,
            execution_state VARCHAR(20) DEFAULT 'pending',
            tool_executions JSON, -- Array of tool execution IDs
            started_at TIMESTAMP WITH TIME ZONE,
            completed_at TIMESTAMP WITH TIME ZONE,
            duration_ms INTEGER,
            error_data JSON,
            feedback JSON,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        -- Runtime configurations table
        CREATE TABLE IF NOT EXISTS runtime_configurations (
            config_id VARCHAR(100) PRIMARY KEY,
            config_type VARCHAR(50) NOT NULL,
            config_data JSON NOT NULL,
            enabled BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        -- Performance metrics table
        CREATE TABLE IF NOT EXISTS performance_metrics (
            metric_id VARCHAR(100) PRIMARY KEY,
            resource_type VARCHAR(50) NOT NULL, -- tool, skill
            resource_id VARCHAR(100) NOT NULL,
            metric_type VARCHAR(50) NOT NULL,
            metric_value NUMERIC(10,3),
            metric_data JSON,
            timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        -- Create indexes for performance
        CREATE INDEX IF NOT EXISTS idx_tool_type ON tool_definitions(tool_type);
        CREATE INDEX IF NOT EXISTS idx_tool_enabled ON tool_definitions(enabled);
        CREATE INDEX IF NOT EXISTS idx_skill_type ON skill_definitions(skill_type);
        CREATE INDEX IF NOT EXISTS idx_skill_enabled ON skill_definitions(enabled);
        CREATE INDEX IF NOT EXISTS idx_tool_exec_state ON tool_executions(execution_state);
        CREATE INDEX IF NOT EXISTS idx_skill_exec_state ON skill_executions(execution_state);
        CREATE INDEX IF NOT EXISTS idx_config_type ON runtime_configurations(config_type);
        CREATE INDEX IF NOT EXISTS idx_metrics_resource ON performance_metrics(resource_type, resource_id);
        """

        async with self.pool.acquire() as conn:
            await conn.execute(schema_sql)
            logger.info("Tool platform schema initialized")

    # Tool Definitions
    async def create_tool(self, tool_id: str, tool_name: str,
                        tool_type: str, tool_spec: Dict,
                        version: str = "1.0", metadata: Optional[Dict] = None) -> None:
        """Store tool definition"""
        query = """
        INSERT INTO tool_definitions (tool_id, tool_name, tool_type, tool_spec, version, metadata_json)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (tool_id)
        DO UPDATE SET
            tool_name = $2,
            tool_type = $3,
            tool_spec = $4,
            version = $5,
            metadata_json = $6,
            updated_at = NOW()
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                tool_id,
                tool_name,
                tool_type,
                json.dumps(tool_spec),
                version,
                json.dumps(metadata) if metadata else None
            )

    async def get_tool(self, tool_id: str) -> Optional[Dict]:
        """Retrieve tool definition"""
        query = """
        SELECT tool_id, tool_name, tool_type, tool_spec, version, enabled, metadata_json, created_at, updated_at
        FROM tool_definitions WHERE tool_id = $1
        """

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, tool_id)
            if row:
                return {
                    "tool_id": row["tool_id"],
                    "tool_name": row["tool_name"],
                    "tool_type": row["tool_type"],
                    "tool_spec": json.loads(row["tool_spec"]),
                    "version": row["version"],
                    "enabled": row["enabled"],
                    "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                }
        return None

    async def list_tools_by_type(self, tool_type: str) -> List[Dict]:
        """List tools by type"""
        query = """
        SELECT tool_id, tool_name, tool_type, tool_spec, version, enabled, metadata_json, created_at, updated_at
        FROM tool_definitions WHERE tool_type = $1 AND enabled = TRUE
        ORDER BY created_at DESC
        """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, tool_type)
            return [
                {
                    "tool_id": row["tool_id"],
                    "tool_name": row["tool_name"],
                    "tool_type": row["tool_type"],
                    "tool_spec": json.loads(row["tool_spec"]),
                    "version": row["version"],
                    "enabled": row["enabled"],
                    "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                }
                for row in rows
            ]

    # Skill Definitions
    async def create_skill(self, skill_id: str, skill_name: str,
                         skill_type: str, skill_manifest: Dict,
                         dependencies: List[str], version: str = "1.0",
                         metadata: Optional[Dict] = None) -> None:
        """Store skill definition"""
        query = """
        INSERT INTO skill_definitions (skill_id, skill_name, skill_type, skill_manifest, version, dependencies, metadata_json)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (skill_id)
        DO UPDATE SET
            skill_name = $2,
            skill_type = $3,
            skill_manifest = $4,
            version = $5,
            dependencies = $6,
            metadata_json = $7,
            updated_at = NOW()
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                skill_id,
                skill_name,
                skill_type,
                json.dumps(skill_manifest),
                version,
                json.dumps(dependencies),
                json.dumps(metadata) if metadata else None
            )

    async def get_skill(self, skill_id: str) -> Optional[Dict]:
        """Retrieve skill definition"""
        query = """
        SELECT skill_id, skill_name, skill_type, skill_manifest, version, enabled, dependencies, metadata_json, created_at, updated_at
        FROM skill_definitions WHERE skill_id = $1
        """

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, skill_id)
            if row:
                return {
                    "skill_id": row["skill_id"],
                    "skill_name": row["skill_name"],
                    "skill_type": row["skill_type"],
                    "skill_manifest": json.loads(row["skill_manifest"]),
                    "version": row["version"],
                    "enabled": row["enabled"],
                    "dependencies": json.loads(row["dependencies"]),
                    "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                }
        return None

    async def list_skills(self, enabled_only: bool = True) -> List[Dict]:
        """List skills"""
        if enabled_only:
            query = """
            SELECT skill_id, skill_name, skill_type, skill_manifest, version, enabled, dependencies, metadata_json, created_at, updated_at
            FROM skill_definitions WHERE enabled = TRUE
            ORDER BY created_at DESC
            """
        else:
            query = """
            SELECT skill_id, skill_name, skill_type, skill_manifest, version, enabled, dependencies, metadata_json, created_at, updated_at
            FROM skill_definitions
            ORDER BY created_at DESC
            """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query)
            return [
                {
                    "skill_id": row["skill_id"],
                    "skill_name": row["skill_name"],
                    "skill_type": row["skill_type"],
                    "skill_manifest": json.loads(row["skill_manifest"]),
                    "version": row["version"],
                    "enabled": row["enabled"],
                    "dependencies": json.loads(row["dependencies"]),
                    "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                }
                for row in rows
            ]

    # Tool Executions
    async def create_tool_execution(self, execution_id: str, tool_id: str,
                                  execution_context: Dict, input_data: Dict) -> None:
        """Create tool execution record"""
        query = """
        INSERT INTO tool_executions (execution_id, tool_id, execution_context, input_data, started_at)
        VALUES ($1, $2, $3, $4, NOW())
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                execution_id,
                tool_id,
                json.dumps(execution_context),
                json.dumps(input_data)
            )

    async def update_tool_execution(self, execution_id: str, execution_state: str,
                                  output_data: Optional[Dict] = None,
                                  error_data: Optional[Dict] = None,
                                  duration_ms: Optional[int] = None) -> None:
        """Update tool execution state and results"""
        query = """
        UPDATE tool_executions
        SET execution_state = $1,
            output_data = $2,
            error_data = $3,
            duration_ms = $4,
            completed_at = CASE WHEN $1 IN ('completed', 'failed') THEN NOW() ELSE completed_at END
        WHERE execution_id = $5
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                execution_state,
                json.dumps(output_data) if output_data else None,
                json.dumps(error_data) if error_data else None,
                duration_ms,
                execution_id
            )

    # Skill Executions
    async def create_skill_execution(self, execution_id: str, skill_id: str,
                                   context_pack: Dict, input_data: Dict) -> None:
        """Create skill execution record"""
        query = """
        INSERT INTO skill_executions (execution_id, skill_id, context_pack, input_data, started_at)
        VALUES ($1, $2, $3, $4, NOW())
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                execution_id,
                skill_id,
                json.dumps(context_pack),
                json.dumps(input_data)
            )

    async def update_skill_execution(self, execution_id: str, execution_state: str,
                                   output_data: Optional[Dict] = None,
                                   tool_executions: Optional[List[str]] = None,
                                   error_data: Optional[Dict] = None,
                                   duration_ms: Optional[int] = None) -> None:
        """Update skill execution state and results"""
        query = """
        UPDATE skill_executions
        SET execution_state = $1,
            output_data = $2,
            tool_executions = $3,
            error_data = $4,
            duration_ms = $5,
            completed_at = CASE WHEN $1 IN ('completed', 'failed') THEN NOW() ELSE completed_at END
        WHERE execution_id = $6
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                execution_state,
                json.dumps(output_data) if output_data else None,
                json.dumps(tool_executions) if tool_executions else None,
                json.dumps(error_data) if error_data else None,
                duration_ms,
                execution_id
            )

    async def add_skill_feedback(self, execution_id: str, feedback: Dict) -> None:
        """Add feedback to skill execution"""
        query = """
        UPDATE skill_executions
        SET feedback = $1
        WHERE execution_id = $2
        """

        async with self.pool.acquire() as conn:
            await conn.execute(query, json.dumps(feedback), execution_id)

    # Runtime Configurations
    async def create_runtime_config(self, config_id: str, config_type: str,
                                  config_data: Dict) -> None:
        """Create or update runtime configuration"""
        query = """
        INSERT INTO runtime_configurations (config_id, config_type, config_data)
        VALUES ($1, $2, $3)
        ON CONFLICT (config_id)
        DO UPDATE SET
            config_type = $2,
            config_data = $3,
            updated_at = NOW()
        """

        async with self.pool.acquire() as conn:
            await conn.execute(query, config_id, config_type, json.dumps(config_data))

    async def get_runtime_config(self, config_id: str) -> Optional[Dict]:
        """Get runtime configuration"""
        query = """
        SELECT config_id, config_type, config_data, enabled, created_at, updated_at
        FROM runtime_configurations WHERE config_id = $1
        """

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, config_id)
            if row:
                return {
                    "config_id": row["config_id"],
                    "config_type": row["config_type"],
                    "config_data": json.loads(row["config_data"]),
                    "enabled": row["enabled"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                }
        return None

    # Performance Metrics
    async def record_performance_metric(self, metric_id: str, resource_type: str,
                                      resource_id: str, metric_type: str,
                                      metric_value: float, metric_data: Optional[Dict] = None) -> None:
        """Record performance metric"""
        query = """
        INSERT INTO performance_metrics (metric_id, resource_type, resource_id, metric_type, metric_value, metric_data)
        VALUES ($1, $2, $3, $4, $5, $6)
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                metric_id,
                resource_type,
                resource_id,
                metric_type,
                metric_value,
                json.dumps(metric_data) if metric_data else None
            )

    async def get_performance_metrics(self, resource_type: str, resource_id: str,
                                    metric_type: Optional[str] = None,
                                    limit: int = 100) -> List[Dict]:
        """Get performance metrics"""
        if metric_type:
            query = """
            SELECT metric_id, resource_type, resource_id, metric_type, metric_value, metric_data, timestamp
            FROM performance_metrics
            WHERE resource_type = $1 AND resource_id = $2 AND metric_type = $3
            ORDER BY timestamp DESC
            LIMIT $4
            """
            params = [resource_type, resource_id, metric_type, limit]
        else:
            query = """
            SELECT metric_id, resource_type, resource_id, metric_type, metric_value, metric_data, timestamp
            FROM performance_metrics
            WHERE resource_type = $1 AND resource_id = $2
            ORDER BY timestamp DESC
            LIMIT $3
            """
            params = [resource_type, resource_id, limit]

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [
                {
                    "metric_id": row["metric_id"],
                    "resource_type": row["resource_type"],
                    "resource_id": row["resource_id"],
                    "metric_type": row["metric_type"],
                    "metric_value": row["metric_value"],
                    "metric_data": json.loads(row["metric_data"]) if row["metric_data"] else {},
                    "timestamp": row["timestamp"]
                }
                for row in rows
            ]

    async def close(self) -> None:
        """Close connection pool"""
        if self.pool:
            await self.pool.close()