"""
PostgreSQL store for policy enforcer service

Handles policy decision logs, policy definitions, statistics, and configuration.
"""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any
from uuid import uuid4

import asyncpg
from asyncpg import Pool

logger = logging.getLogger(__name__)


class PostgreSQLPolicyStore:
    """PostgreSQL storage for policy enforcer data"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[Pool] = None

    async def initialize(self):
        """Initialize the connection pool"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=15,
                command_timeout=60
            )
            logger.info("PostgreSQL policy store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize policy store: {e}")
            raise

    async def close(self):
        """Close the connection pool"""
        if self.pool:
            await self.pool.close()

    @asynccontextmanager
    async def transaction(self):
        """Context manager for database transactions"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    async def log_policy_decision(self, decision_log: Dict[str, Any]) -> None:
        """Log a policy decision"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO policy_decisions (
                    id, timestamp, session_id, user_id, capability, resource,
                    effect, reason, determining_layer, determining_rule_id,
                    observe_mode, enforced, trace, tenant, legacy_effect,
                    legacy_reason, stage
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17
                )
            """,
                str(uuid4()), decision_log["timestamp"], decision_log["session_id"],
                decision_log["user_id"], decision_log["capability"], decision_log.get("resource"),
                decision_log["effect"], decision_log["reason"], decision_log["determining_layer"],
                decision_log["determining_rule_id"], decision_log.get("observe_mode", False),
                decision_log.get("enforced", False), json.dumps(decision_log.get("trace", [])),
                decision_log.get("tenant"), decision_log.get("legacy_effect"),
                decision_log.get("legacy_reason"), decision_log.get("stage", "unknown")
            )

    async def get_policy_decisions(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        capability: Optional[str] = None,
        effect: Optional[str] = None,
        hours: int = 24,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Get policy decisions with filtering"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        query_parts = [
            "SELECT * FROM policy_decisions WHERE timestamp >= $1"
        ]
        params = [cutoff_time]
        param_count = 1

        if session_id:
            param_count += 1
            query_parts.append(f"AND session_id = ${param_count}")
            params.append(session_id)

        if user_id:
            param_count += 1
            query_parts.append(f"AND user_id = ${param_count}")
            params.append(user_id)

        if capability:
            param_count += 1
            query_parts.append(f"AND capability = ${param_count}")
            params.append(capability)

        if effect:
            param_count += 1
            query_parts.append(f"AND effect = ${param_count}")
            params.append(effect)

        query_parts.append("ORDER BY timestamp DESC")
        param_count += 1
        query_parts.append(f"LIMIT ${param_count}")
        params.append(limit)

        query = " ".join(query_parts)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [dict(row) for row in rows]

    async def store_policy(self, policy_data: Dict[str, Any]) -> None:
        """Store a policy definition"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO policies (
                    id, version, layer, api_version, kind, metadata, policy_data, created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9
                )
                ON CONFLICT (id, version) DO UPDATE SET
                    policy_data = EXCLUDED.policy_data,
                    updated_at = EXCLUDED.updated_at
            """,
                policy_data["metadata"]["id"], policy_data["metadata"].get("version", "1.0.0"),
                policy_data["metadata"].get("layer", "unknown"), policy_data.get("apiVersion"),
                policy_data.get("kind"), json.dumps(policy_data.get("metadata", {})),
                json.dumps(policy_data), datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat()
            )

    async def get_policy(self, policy_id: str, version: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get a policy definition"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            if version:
                row = await conn.fetchrow(
                    "SELECT policy_data FROM policies WHERE id = $1 AND version = $2",
                    policy_id, version
                )
            else:
                # Get latest version
                row = await conn.fetchrow("""
                    SELECT policy_data FROM policies
                    WHERE id = $1
                    ORDER BY created_at DESC
                    LIMIT 1
                """, policy_id)

            if row:
                return json.loads(row["policy_data"])
            return None

    async def get_policies_by_layer(self, layer: str) -> List[Dict[str, Any]]:
        """Get all policies for a specific layer"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT policy_data FROM policies WHERE layer = $1 ORDER BY id",
                layer
            )
            return [json.loads(row["policy_data"]) for row in rows]

    async def store_session_policy(self, session_id: str, policy_overrides: Dict[str, Any]) -> None:
        """Store session-specific policy overrides"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO session_policies (session_id, policy_overrides, created_at, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (session_id) DO UPDATE SET
                    policy_overrides = EXCLUDED.policy_overrides,
                    updated_at = EXCLUDED.updated_at
            """,
                session_id, json.dumps(policy_overrides),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat()
            )

    async def get_session_policy(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session-specific policy overrides"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT policy_overrides FROM session_policies WHERE session_id = $1",
                session_id
            )
            return json.loads(row["policy_overrides"]) if row else None

    async def store_context_policy(self, context_key: str, policy_data: Dict[str, Any]) -> None:
        """Store context-specific policy (repo/tenant specific)"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO context_policies (context_key, policy_data, created_at, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (context_key) DO UPDATE SET
                    policy_data = EXCLUDED.policy_data,
                    updated_at = EXCLUDED.updated_at
            """,
                context_key, json.dumps(policy_data),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat()
            )

    async def get_context_policy(self, context_key: str) -> Optional[Dict[str, Any]]:
        """Get context-specific policy"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT policy_data FROM context_policies WHERE context_key = $1",
                context_key
            )
            return json.loads(row["policy_data"]) if row else None

    async def get_policy_statistics(self, hours: int = 24) -> Dict[str, Any]:
        """Get policy decision statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self.pool.acquire() as conn:
            # Effect distribution
            effect_stats = await conn.fetch("""
                SELECT effect, COUNT(*) as count
                FROM policy_decisions
                WHERE timestamp >= $1
                GROUP BY effect
            """, cutoff_time)

            # Layer distribution
            layer_stats = await conn.fetch("""
                SELECT determining_layer, COUNT(*) as count
                FROM policy_decisions
                WHERE timestamp >= $1
                GROUP BY determining_layer
                ORDER BY count DESC
            """, cutoff_time)

            # Capability distribution
            capability_stats = await conn.fetch("""
                SELECT capability, COUNT(*) as count
                FROM policy_decisions
                WHERE timestamp >= $1
                GROUP BY capability
                ORDER BY count DESC
                LIMIT 20
            """, cutoff_time)

            # Enforcement statistics
            enforcement_stats = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total_decisions,
                    COUNT(*) FILTER (WHERE enforced = TRUE) as enforced_decisions,
                    COUNT(*) FILTER (WHERE enforced = FALSE) as shadow_decisions,
                    COUNT(*) FILTER (WHERE effect = 'deny' AND observe_mode = TRUE) as would_deny_count,
                    COUNT(*) FILTER (WHERE legacy_effect IS NOT NULL AND effect != legacy_effect) as divergence_count
                FROM policy_decisions
                WHERE timestamp >= $1
            """, cutoff_time)

            # User activity
            user_stats = await conn.fetch("""
                SELECT user_id, COUNT(*) as decisions
                FROM policy_decisions
                WHERE timestamp >= $1
                GROUP BY user_id
                ORDER BY decisions DESC
                LIMIT 10
            """, cutoff_time)

            # Session activity
            session_stats = await conn.fetch("""
                SELECT session_id, COUNT(*) as decisions, user_id
                FROM policy_decisions
                WHERE timestamp >= $1
                GROUP BY session_id, user_id
                ORDER BY decisions DESC
                LIMIT 10
            """, cutoff_time)

            return {
                "time_window_hours": hours,
                "cutoff_time": cutoff_time,
                "effect_distribution": {row["effect"]: row["count"] for row in effect_stats},
                "layer_distribution": {row["determining_layer"]: row["count"] for row in layer_stats},
                "capability_distribution": {row["capability"]: row["count"] for row in capability_stats},
                "enforcement": dict(enforcement_stats) if enforcement_stats else {},
                "top_users": [{"user_id": row["user_id"], "decisions": row["decisions"]} for row in user_stats],
                "active_sessions": [{"session_id": row["session_id"], "user_id": row["user_id"], "decisions": row["decisions"]} for row in session_stats]
            }

    async def log_divergence_event(self, divergence_data: Dict[str, Any]) -> None:
        """Log a policy divergence event"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO policy_divergences (
                    id, timestamp, session_id, capability, resource, new_decision,
                    new_reason, legacy_decision, legacy_reason, stage
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10
                )
            """,
                str(uuid4()), divergence_data["timestamp"], divergence_data["session_id"],
                divergence_data["capability"], divergence_data.get("resource"),
                divergence_data["new_decision"], divergence_data["new_reason"],
                divergence_data["legacy_decision"], divergence_data["legacy_reason"],
                divergence_data.get("stage", "unknown")
            )

    async def get_recent_divergences(self, hours: int = 24, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent policy divergence events"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM policy_divergences
                WHERE timestamp >= $1
                ORDER BY timestamp DESC
                LIMIT $2
            """, cutoff_time, limit)
            return [dict(row) for row in rows]

    async def store_configuration(self, config_key: str, config_data: Dict[str, Any]) -> None:
        """Store configuration settings"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO policy_configurations (config_key, config_data, created_at, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (config_key) DO UPDATE SET
                    config_data = EXCLUDED.config_data,
                    updated_at = EXCLUDED.updated_at
            """,
                config_key, json.dumps(config_data),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat()
            )

    async def get_configuration(self, config_key: str) -> Optional[Dict[str, Any]]:
        """Get configuration settings"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT config_data FROM policy_configurations WHERE config_key = $1",
                config_key
            )
            return json.loads(row["config_data"]) if row else None

    async def cleanup_old_decisions(self, days: int = 30) -> int:
        """Clean up old policy decision logs"""
        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.transaction() as conn:
            result = await conn.execute(
                "DELETE FROM policy_decisions WHERE timestamp < $1", cutoff_time
            )
            # Extract number from result like "DELETE 123"
            deleted_count = int(result.split()[-1]) if result.split()[-1].isdigit() else 0
            return deleted_count

    async def get_health_status(self) -> Dict[str, Any]:
        """Get database health status"""
        if not self.pool:
            return {"status": "disconnected", "pool": None}

        try:
            async with self.pool.acquire() as conn:
                # Test basic connectivity
                result = await conn.fetchval("SELECT 1")

                # Get table counts
                policy_count = await conn.fetchval("SELECT COUNT(*) FROM policies")
                decision_count = await conn.fetchval("SELECT COUNT(*) FROM policy_decisions")
                session_policy_count = await conn.fetchval("SELECT COUNT(*) FROM session_policies")

                return {
                    "status": "healthy" if result == 1 else "unhealthy",
                    "pool_size": self.pool.get_size(),
                    "policies_stored": policy_count,
                    "decisions_logged": decision_count,
                    "session_policies": session_policy_count,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }


# Global store instance
policy_store: Optional[PostgreSQLPolicyStore] = None


async def get_policy_store() -> PostgreSQLPolicyStore:
    """Get the global policy store instance"""
    global policy_store
    if policy_store is None:
        raise RuntimeError("Policy store not initialized")
    return policy_store


async def initialize_policy_store(database_url: str):
    """Initialize the global policy store"""
    global policy_store
    policy_store = PostgreSQLPolicyStore(database_url)
    await policy_store.initialize()


async def close_policy_store():
    """Close the global policy store"""
    global policy_store
    if policy_store:
        await policy_store.close()
        policy_store = None