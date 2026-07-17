"""
PostgreSQL Store for Policy Management Service

Provides database persistence for policies, constitution, validation rules,
and governance operations following the database-per-service pattern.
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


class PostgreSQLPolicyStore(BaseStore):
    """
    PostgreSQL persistence layer for policy management operations

    Handles:
    - Policy definitions and evolution
    - Constitution and governance rules
    - Validation rules and constraints
    - Review queues and approval gates
    - Policy adoption and scoring
    """

    def __init__(self, connection_pool: asyncpg.Pool):
        self.pool = connection_pool

    @classmethod
    async def create(cls, database_url: str) -> 'PostgreSQLPolicyStore':
        """Create store with connection pool"""
        try:
            pool = await asyncpg.create_pool(database_url, min_size=1, max_size=10)
            store = cls(pool)
            await store.initialize_schema()
            logger.info("PostgreSQL policy store initialized")
            return store
        except Exception as e:
            logger.error(f"Failed to create PostgreSQL policy store: {e}")
            raise

    async def initialize_schema(self) -> None:
        """Initialize database schema if not exists"""
        schema_sql = """
        -- Policy definitions table
        CREATE TABLE IF NOT EXISTS policy_definitions (
            policy_id VARCHAR(100) PRIMARY KEY,
            policy_name VARCHAR(200) NOT NULL,
            policy_type VARCHAR(50) NOT NULL,
            policy_content JSON NOT NULL,
            version VARCHAR(20) DEFAULT '1.0',
            status VARCHAR(20) DEFAULT 'draft',
            metadata_json JSON,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        -- Constitution and governance table
        CREATE TABLE IF NOT EXISTS constitution (
            constitution_id VARCHAR(100) PRIMARY KEY,
            constitution_name VARCHAR(200) NOT NULL,
            constitution_content JSON NOT NULL,
            version VARCHAR(20) DEFAULT '1.0',
            status VARCHAR(20) DEFAULT 'draft',
            effective_date TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        -- Policy validation rules table
        CREATE TABLE IF NOT EXISTS validation_rules (
            rule_id VARCHAR(100) PRIMARY KEY,
            rule_name VARCHAR(200) NOT NULL,
            rule_type VARCHAR(50) NOT NULL,
            rule_content JSON NOT NULL,
            target_policies JSON, -- Array of policy types this rule applies to
            severity VARCHAR(20) DEFAULT 'error',
            enabled BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        -- Review queue table
        CREATE TABLE IF NOT EXISTS review_queue (
            review_id VARCHAR(100) PRIMARY KEY,
            policy_id VARCHAR(100) REFERENCES policy_definitions(policy_id),
            review_type VARCHAR(50) NOT NULL,
            status VARCHAR(20) DEFAULT 'pending',
            reviewer_id VARCHAR(100),
            review_data JSON,
            priority INTEGER DEFAULT 0,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            reviewed_at TIMESTAMP WITH TIME ZONE
        );

        -- Policy adoption tracking table
        CREATE TABLE IF NOT EXISTS policy_adoption (
            adoption_id VARCHAR(100) PRIMARY KEY,
            policy_id VARCHAR(100) REFERENCES policy_definitions(policy_id),
            context_id VARCHAR(100),
            adoption_status VARCHAR(20) DEFAULT 'proposed',
            score NUMERIC(10,3),
            adoption_data JSON,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        -- Approval gates table
        CREATE TABLE IF NOT EXISTS approval_gates (
            gate_id VARCHAR(100) PRIMARY KEY,
            gate_name VARCHAR(200) NOT NULL,
            gate_type VARCHAR(50) NOT NULL,
            gate_config JSON NOT NULL,
            policies JSON, -- Array of policies this gate applies to
            enabled BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        -- Policy evolution tracking
        CREATE TABLE IF NOT EXISTS policy_evolution (
            evolution_id VARCHAR(100) PRIMARY KEY,
            policy_id VARCHAR(100) REFERENCES policy_definitions(policy_id),
            evolution_type VARCHAR(50) NOT NULL,
            old_version VARCHAR(20),
            new_version VARCHAR(20),
            changes JSON NOT NULL,
            reason TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );

        -- Create indexes for performance
        CREATE INDEX IF NOT EXISTS idx_policy_type ON policy_definitions(policy_type);
        CREATE INDEX IF NOT EXISTS idx_policy_status ON policy_definitions(status);
        CREATE INDEX IF NOT EXISTS idx_constitution_status ON constitution(status);
        CREATE INDEX IF NOT EXISTS idx_validation_rule_type ON validation_rules(rule_type);
        CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status);
        CREATE INDEX IF NOT EXISTS idx_adoption_status ON policy_adoption(adoption_status);
        CREATE INDEX IF NOT EXISTS idx_gate_type ON approval_gates(gate_type);
        """

        async with self.pool.acquire() as conn:
            await conn.execute(schema_sql)
            logger.info("Policy management schema initialized")

    # Policy Definitions
    async def create_policy(self, policy_id: str, policy_name: str,
                          policy_type: str, policy_content: Dict,
                          version: str = "1.0", metadata: Optional[Dict] = None) -> None:
        """Store policy definition"""
        query = """
        INSERT INTO policy_definitions (policy_id, policy_name, policy_type, policy_content, version, metadata_json)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (policy_id)
        DO UPDATE SET
            policy_name = $2,
            policy_type = $3,
            policy_content = $4,
            version = $5,
            metadata_json = $6,
            updated_at = NOW()
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                policy_id,
                policy_name,
                policy_type,
                json.dumps(policy_content),
                version,
                json.dumps(metadata) if metadata else None
            )

    async def get_policy(self, policy_id: str) -> Optional[Dict]:
        """Retrieve policy definition"""
        query = """
        SELECT policy_id, policy_name, policy_type, policy_content, version, status, metadata_json, created_at, updated_at
        FROM policy_definitions WHERE policy_id = $1
        """

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, policy_id)
            if row:
                return {
                    "policy_id": row["policy_id"],
                    "policy_name": row["policy_name"],
                    "policy_type": row["policy_type"],
                    "policy_content": json.loads(row["policy_content"]),
                    "version": row["version"],
                    "status": row["status"],
                    "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                }
        return None

    async def list_policies_by_type(self, policy_type: str) -> List[Dict]:
        """List policies by type"""
        query = """
        SELECT policy_id, policy_name, policy_type, policy_content, version, status, metadata_json, created_at, updated_at
        FROM policy_definitions WHERE policy_type = $1
        ORDER BY created_at DESC
        """

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, policy_type)
            return [
                {
                    "policy_id": row["policy_id"],
                    "policy_name": row["policy_name"],
                    "policy_type": row["policy_type"],
                    "policy_content": json.loads(row["policy_content"]),
                    "version": row["version"],
                    "status": row["status"],
                    "metadata": json.loads(row["metadata_json"]) if row["metadata_json"] else {},
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"]
                }
                for row in rows
            ]

    # Constitution Management
    async def create_constitution(self, constitution_id: str, constitution_name: str,
                                constitution_content: Dict, version: str = "1.0") -> None:
        """Store constitution"""
        query = """
        INSERT INTO constitution (constitution_id, constitution_name, constitution_content, version)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (constitution_id)
        DO UPDATE SET
            constitution_name = $2,
            constitution_content = $3,
            version = $4,
            updated_at = NOW()
        """

        async with self.pool.acquire() as conn:
            await conn.execute(query, constitution_id, constitution_name, json.dumps(constitution_content), version)

    async def get_active_constitution(self) -> Optional[Dict]:
        """Get active constitution"""
        query = """
        SELECT constitution_id, constitution_name, constitution_content, version, status, effective_date, created_at
        FROM constitution
        WHERE status = 'active'
        ORDER BY effective_date DESC
        LIMIT 1
        """

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query)
            if row:
                return {
                    "constitution_id": row["constitution_id"],
                    "constitution_name": row["constitution_name"],
                    "constitution_content": json.loads(row["constitution_content"]),
                    "version": row["version"],
                    "status": row["status"],
                    "effective_date": row["effective_date"],
                    "created_at": row["created_at"]
                }
        return None

    # Validation Rules
    async def create_validation_rule(self, rule_id: str, rule_name: str,
                                   rule_type: str, rule_content: Dict,
                                   target_policies: List[str], severity: str = "error") -> None:
        """Store validation rule"""
        query = """
        INSERT INTO validation_rules (rule_id, rule_name, rule_type, rule_content, target_policies, severity)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (rule_id)
        DO UPDATE SET
            rule_name = $2,
            rule_type = $3,
            rule_content = $4,
            target_policies = $5,
            severity = $6,
            updated_at = NOW()
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                rule_id,
                rule_name,
                rule_type,
                json.dumps(rule_content),
                json.dumps(target_policies),
                severity
            )

    async def get_validation_rules(self, policy_type: Optional[str] = None) -> List[Dict]:
        """Get validation rules, optionally filtered by policy type"""
        if policy_type:
            query = """
            SELECT rule_id, rule_name, rule_type, rule_content, target_policies, severity, enabled, created_at
            FROM validation_rules
            WHERE enabled = TRUE AND target_policies @> $1
            ORDER BY created_at
            """
            target_filter = json.dumps([policy_type])
        else:
            query = """
            SELECT rule_id, rule_name, rule_type, rule_content, target_policies, severity, enabled, created_at
            FROM validation_rules
            WHERE enabled = TRUE
            ORDER BY created_at
            """
            target_filter = None

        async with self.pool.acquire() as conn:
            if target_filter:
                rows = await conn.fetch(query, target_filter)
            else:
                rows = await conn.fetch(query)

            return [
                {
                    "rule_id": row["rule_id"],
                    "rule_name": row["rule_name"],
                    "rule_type": row["rule_type"],
                    "rule_content": json.loads(row["rule_content"]),
                    "target_policies": json.loads(row["target_policies"]),
                    "severity": row["severity"],
                    "enabled": row["enabled"],
                    "created_at": row["created_at"]
                }
                for row in rows
            ]

    # Review Queue Management
    async def add_to_review_queue(self, review_id: str, policy_id: str,
                                review_type: str, review_data: Dict, priority: int = 0) -> None:
        """Add policy to review queue"""
        query = """
        INSERT INTO review_queue (review_id, policy_id, review_type, review_data, priority)
        VALUES ($1, $2, $3, $4, $5)
        """

        async with self.pool.acquire() as conn:
            await conn.execute(query, review_id, policy_id, review_type, json.dumps(review_data), priority)

    async def get_pending_reviews(self, review_type: Optional[str] = None) -> List[Dict]:
        """Get pending reviews"""
        if review_type:
            query = """
            SELECT r.review_id, r.policy_id, r.review_type, r.status, r.review_data, r.priority, r.created_at,
                   p.policy_name, p.policy_type
            FROM review_queue r
            JOIN policy_definitions p ON r.policy_id = p.policy_id
            WHERE r.status = 'pending' AND r.review_type = $1
            ORDER BY r.priority DESC, r.created_at ASC
            """
        else:
            query = """
            SELECT r.review_id, r.policy_id, r.review_type, r.status, r.review_data, r.priority, r.created_at,
                   p.policy_name, p.policy_type
            FROM review_queue r
            JOIN policy_definitions p ON r.policy_id = p.policy_id
            WHERE r.status = 'pending'
            ORDER BY r.priority DESC, r.created_at ASC
            """

        async with self.pool.acquire() as conn:
            if review_type:
                rows = await conn.fetch(query, review_type)
            else:
                rows = await conn.fetch(query)

            return [
                {
                    "review_id": row["review_id"],
                    "policy_id": row["policy_id"],
                    "policy_name": row["policy_name"],
                    "policy_type": row["policy_type"],
                    "review_type": row["review_type"],
                    "status": row["status"],
                    "review_data": json.loads(row["review_data"]),
                    "priority": row["priority"],
                    "created_at": row["created_at"]
                }
                for row in rows
            ]

    # Policy Adoption
    async def record_adoption(self, adoption_id: str, policy_id: str,
                            context_id: str, adoption_status: str,
                            score: Optional[float] = None, adoption_data: Optional[Dict] = None) -> None:
        """Record policy adoption"""
        query = """
        INSERT INTO policy_adoption (adoption_id, policy_id, context_id, adoption_status, score, adoption_data)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (adoption_id)
        DO UPDATE SET
            adoption_status = $4,
            score = $5,
            adoption_data = $6,
            updated_at = NOW()
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                adoption_id,
                policy_id,
                context_id,
                adoption_status,
                score,
                json.dumps(adoption_data) if adoption_data else None
            )

    # Approval Gates
    async def create_approval_gate(self, gate_id: str, gate_name: str,
                                 gate_type: str, gate_config: Dict, policies: List[str]) -> None:
        """Create approval gate"""
        query = """
        INSERT INTO approval_gates (gate_id, gate_name, gate_type, gate_config, policies)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (gate_id)
        DO UPDATE SET
            gate_name = $2,
            gate_type = $3,
            gate_config = $4,
            policies = $5,
            updated_at = NOW()
        """

        async with self.pool.acquire() as conn:
            await conn.execute(
                query,
                gate_id,
                gate_name,
                gate_type,
                json.dumps(gate_config),
                json.dumps(policies)
            )

    async def get_approval_gates(self, policy_id: Optional[str] = None) -> List[Dict]:
        """Get approval gates"""
        if policy_id:
            query = """
            SELECT gate_id, gate_name, gate_type, gate_config, policies, enabled, created_at
            FROM approval_gates
            WHERE enabled = TRUE AND policies @> $1
            ORDER BY created_at
            """
            policy_filter = json.dumps([policy_id])
        else:
            query = """
            SELECT gate_id, gate_name, gate_type, gate_config, policies, enabled, created_at
            FROM approval_gates
            WHERE enabled = TRUE
            ORDER BY created_at
            """
            policy_filter = None

        async with self.pool.acquire() as conn:
            if policy_filter:
                rows = await conn.fetch(query, policy_filter)
            else:
                rows = await conn.fetch(query)

            return [
                {
                    "gate_id": row["gate_id"],
                    "gate_name": row["gate_name"],
                    "gate_type": row["gate_type"],
                    "gate_config": json.loads(row["gate_config"]),
                    "policies": json.loads(row["policies"]),
                    "enabled": row["enabled"],
                    "created_at": row["created_at"]
                }
                for row in rows
            ]

    async def close(self) -> None:
        """Close connection pool"""
        if self.pool:
            await self.pool.close()