"""
PostgreSQL store for repair-agent service

Handles repair operations, strategies, agent repair history, auto-repair configuration,
analytics, and error pattern analysis.
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


class PostgreSQLRepairStore:
    """PostgreSQL storage for repair-agent data"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[Pool] = None

    async def initialize(self):
        """Initialize the connection pool"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=10,
                command_timeout=60
            )
            logger.info("PostgreSQL repair store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize repair store: {e}")
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

    # === REPAIR OPERATIONS ===

    async def create_repair(
        self,
        repair_id: str,
        agent_id: str,
        execution_id: str,
        repair_type: str,
        repair_data: Dict[str, Any],
        priority: int = 5,
        auto_initiated: bool = False
    ) -> None:
        """Create a new repair operation"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO repair_operations (
                    repair_id, agent_id, execution_id, repair_type, repair_data,
                    status, priority, auto_initiated, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
                repair_id, agent_id, execution_id, repair_type,
                json.dumps(repair_data), "pending", priority, auto_initiated,
                datetime.now(timezone.utc).isoformat()
            )

    async def update_repair_status(
        self,
        repair_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        progress: Optional[int] = None
    ) -> bool:
        """Update repair operation status"""
        async with self.transaction() as conn:
            set_clauses = ["status = $2", "updated_at = $3"]
            params = [repair_id, status, datetime.now(timezone.utc).isoformat()]
            param_count = 3

            if result:
                param_count += 1
                set_clauses.append(f"repair_result = ${param_count}")
                params.append(json.dumps(result))

            if error_message:
                param_count += 1
                set_clauses.append(f"error_message = ${param_count}")
                params.append(error_message)

            if progress is not None:
                param_count += 1
                set_clauses.append(f"progress = ${param_count}")
                params.append(progress)

            if status in ["completed", "failed", "cancelled"]:
                param_count += 1
                set_clauses.append(f"completed_at = ${param_count}")
                params.append(datetime.now(timezone.utc).isoformat())

            query = f"UPDATE repair_operations SET {', '.join(set_clauses)} WHERE repair_id = $1"
            result = await conn.execute(query, *params)
            return "UPDATE 1" in str(result)

    async def get_repair_status(self, repair_id: str) -> Optional[Dict[str, Any]]:
        """Get repair operation status and details"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM repair_operations WHERE repair_id = $1", repair_id
            )

            if not row:
                return None

            result = dict(row)
            # Parse JSON fields
            for json_field in ["repair_data", "repair_result"]:
                if result.get(json_field):
                    try:
                        result[json_field] = json.loads(result[json_field])
                    except json.JSONDecodeError:
                        result[json_field] = {}

            return result

    async def get_repair_history(
        self,
        agent_id: str,
        limit: int = 100,
        status_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get repair history for an agent"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        query_parts = ["SELECT * FROM repair_operations WHERE agent_id = $1"]
        params = [agent_id]
        param_count = 1

        if status_filter:
            param_count += 1
            query_parts.append(f"AND status = ${param_count}")
            params.append(status_filter)

        query_parts.append("ORDER BY created_at DESC")
        param_count += 1
        query_parts.append(f"LIMIT ${param_count}")
        params.append(limit)

        query = " ".join(query_parts)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            results = []
            for row in rows:
                result = dict(row)
                # Parse JSON fields
                for json_field in ["repair_data", "repair_result"]:
                    if result.get(json_field):
                        try:
                            result[json_field] = json.loads(result[json_field])
                        except json.JSONDecodeError:
                            result[json_field] = {}
                results.append(result)
            return results

    async def get_pending_repairs(
        self,
        limit: int = 50,
        priority_threshold: int = 5
    ) -> List[Dict[str, Any]]:
        """Get pending repair operations ordered by priority"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM repair_operations
                WHERE status = 'pending' AND priority <= $1
                ORDER BY priority ASC, created_at ASC
                LIMIT $2
            """, priority_threshold, limit)

            results = []
            for row in rows:
                result = dict(row)
                if result.get("repair_data"):
                    try:
                        result["repair_data"] = json.loads(result["repair_data"])
                    except json.JSONDecodeError:
                        result["repair_data"] = {}
                results.append(result)
            return results

    # === REPAIR STRATEGIES ===

    async def register_repair_strategy(
        self,
        strategy_name: str,
        strategy_config: Dict[str, Any],
        description: str,
        success_rate: float = 0.0,
        avg_duration_seconds: float = 0.0
    ) -> None:
        """Register or update a repair strategy"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO repair_strategies (
                    strategy_name, strategy_config, description, success_rate,
                    avg_duration_seconds, registered_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (strategy_name) DO UPDATE SET
                    strategy_config = EXCLUDED.strategy_config,
                    description = EXCLUDED.description,
                    updated_at = EXCLUDED.updated_at
            """,
                strategy_name, json.dumps(strategy_config), description,
                success_rate, avg_duration_seconds,
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat()
            )

    async def get_repair_strategies(self) -> List[Dict[str, Any]]:
        """Get all registered repair strategies"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM repair_strategies ORDER BY success_rate DESC")
            results = []
            for row in rows:
                result = dict(row)
                if result.get("strategy_config"):
                    try:
                        result["strategy_config"] = json.loads(result["strategy_config"])
                    except json.JSONDecodeError:
                        result["strategy_config"] = {}
                results.append(result)
            return results

    async def update_strategy_metrics(
        self,
        strategy_name: str,
        success: bool,
        duration_seconds: float
    ) -> None:
        """Update strategy success rate and duration metrics"""
        async with self.transaction() as conn:
            # Get current metrics
            current = await conn.fetchrow(
                "SELECT success_rate, avg_duration_seconds, usage_count FROM repair_strategies WHERE strategy_name = $1",
                strategy_name
            )

            if current:
                # Calculate new metrics (weighted average)
                old_count = current["usage_count"] or 0
                new_count = old_count + 1
                old_success_rate = current["success_rate"] or 0.0
                old_avg_duration = current["avg_duration_seconds"] or 0.0

                # Update success rate
                if success:
                    new_success_rate = (old_success_rate * old_count + 1.0) / new_count
                else:
                    new_success_rate = (old_success_rate * old_count) / new_count

                # Update average duration
                new_avg_duration = (old_avg_duration * old_count + duration_seconds) / new_count

                await conn.execute("""
                    UPDATE repair_strategies
                    SET success_rate = $2, avg_duration_seconds = $3, usage_count = $4, updated_at = $5
                    WHERE strategy_name = $1
                """,
                    strategy_name, new_success_rate, new_avg_duration, new_count,
                    datetime.now(timezone.utc).isoformat()
                )

    # === AUTO-REPAIR CONFIGURATION ===

    async def set_auto_repair_config(
        self,
        agent_id: str,
        enabled: bool,
        strategies: List[str],
        max_retries: int = 3,
        escalation_threshold: int = 2,
        config: Optional[Dict[str, Any]] = None
    ) -> None:
        """Set auto-repair configuration for an agent"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO auto_repair_config (
                    agent_id, enabled, strategies, max_retries, escalation_threshold,
                    config, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (agent_id) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    strategies = EXCLUDED.strategies,
                    max_retries = EXCLUDED.max_retries,
                    escalation_threshold = EXCLUDED.escalation_threshold,
                    config = EXCLUDED.config,
                    updated_at = EXCLUDED.updated_at
            """,
                agent_id, enabled, json.dumps(strategies), max_retries,
                escalation_threshold, json.dumps(config or {}),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat()
            )

    async def get_auto_repair_config(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get auto-repair configuration for an agent"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM auto_repair_config WHERE agent_id = $1", agent_id
            )

            if not row:
                return None

            result = dict(row)
            # Parse JSON fields
            if result.get("strategies"):
                try:
                    result["strategies"] = json.loads(result["strategies"])
                except json.JSONDecodeError:
                    result["strategies"] = []

            if result.get("config"):
                try:
                    result["config"] = json.loads(result["config"])
                except json.JSONDecodeError:
                    result["config"] = {}

            return result

    async def get_agents_with_auto_repair(self) -> List[Dict[str, Any]]:
        """Get all agents with auto-repair enabled"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM auto_repair_config WHERE enabled = true ORDER BY agent_id"
            )

            results = []
            for row in rows:
                result = dict(row)
                # Parse JSON fields
                if result.get("strategies"):
                    try:
                        result["strategies"] = json.loads(result["strategies"])
                    except json.JSONDecodeError:
                        result["strategies"] = []

                if result.get("config"):
                    try:
                        result["config"] = json.loads(result["config"])
                    except json.JSONDecodeError:
                        result["config"] = {}

                results.append(result)
            return results

    # === ERROR PATTERN ANALYSIS ===

    async def log_error_pattern(
        self,
        agent_id: str,
        execution_id: str,
        error_type: str,
        error_message: str,
        error_context: Dict[str, Any],
        repair_attempted: bool = False
    ) -> str:
        """Log an error pattern for analysis"""
        pattern_id = str(uuid4())

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO error_patterns (
                    pattern_id, agent_id, execution_id, error_type, error_message,
                    error_context, repair_attempted, occurred_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
                pattern_id, agent_id, execution_id, error_type, error_message,
                json.dumps(error_context), repair_attempted,
                datetime.now(timezone.utc).isoformat()
            )

        return pattern_id

    async def analyze_error_patterns(
        self,
        agent_id: Optional[str] = None,
        days: int = 7
    ) -> Dict[str, Any]:
        """Analyze error patterns to identify trends"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.pool.acquire() as conn:
            # Build base query
            base_where = "WHERE occurred_at >= $1"
            params = [cutoff_time]

            if agent_id:
                base_where += " AND agent_id = $2"
                params.append(agent_id)

            # Error type frequency
            error_types_query = f"""
                SELECT error_type, COUNT(*) as count
                FROM error_patterns
                {base_where}
                GROUP BY error_type
                ORDER BY count DESC
                LIMIT 10
            """
            error_types = await conn.fetch(error_types_query, *params)

            # Most frequent error messages
            error_messages_query = f"""
                SELECT error_message, COUNT(*) as count
                FROM error_patterns
                {base_where}
                GROUP BY error_message
                ORDER BY count DESC
                LIMIT 10
            """
            error_messages = await conn.fetch(error_messages_query, *params)

            # Agents with most errors (if not filtered by agent)
            if not agent_id:
                error_agents_query = f"""
                    SELECT agent_id, COUNT(*) as error_count
                    FROM error_patterns
                    {base_where}
                    GROUP BY agent_id
                    ORDER BY error_count DESC
                    LIMIT 10
                """
                error_agents = await conn.fetch(error_agents_query, *params)
            else:
                error_agents = []

            # Repair success rate
            repair_stats = await conn.fetchrow(f"""
                SELECT
                    COUNT(*) as total_errors,
                    COUNT(*) FILTER (WHERE repair_attempted = true) as repairs_attempted,
                    COUNT(*) FILTER (WHERE repair_attempted = true AND EXISTS (
                        SELECT 1 FROM repair_operations ro
                        WHERE ro.execution_id = error_patterns.execution_id
                        AND ro.status = 'completed'
                    )) as repairs_successful
                FROM error_patterns
                {base_where}
            """, *params)

            return {
                "time_window_days": days,
                "analysis_scope": "agent_specific" if agent_id else "global",
                "target_agent": agent_id,
                "error_type_frequency": [{"error_type": row["error_type"], "count": row["count"]} for row in error_types],
                "frequent_error_messages": [{"message": row["error_message"], "count": row["count"]} for row in error_messages],
                "agents_with_most_errors": [{"agent_id": row["agent_id"], "count": row["error_count"]} for row in error_agents],
                "repair_statistics": dict(repair_stats) if repair_stats else {}
            }

    # === REPAIR ANALYTICS ===

    async def get_repair_analytics(
        self,
        agent_id: Optional[str] = None,
        days: int = 30
    ) -> Dict[str, Any]:
        """Get comprehensive repair analytics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.pool.acquire() as conn:
            # Build base query
            base_where = "WHERE created_at >= $1"
            params = [cutoff_time]

            if agent_id:
                base_where += " AND agent_id = $2"
                params.append(agent_id)

            # Overall repair statistics
            overall_stats = await conn.fetchrow(f"""
                SELECT
                    COUNT(*) as total_repairs,
                    COUNT(*) FILTER (WHERE status = 'completed') as successful_repairs,
                    COUNT(*) FILTER (WHERE status = 'failed') as failed_repairs,
                    COUNT(*) FILTER (WHERE status = 'pending') as pending_repairs,
                    COUNT(*) FILTER (WHERE auto_initiated = true) as auto_repairs,
                    AVG(EXTRACT(EPOCH FROM (completed_at::timestamp - created_at::timestamp))) as avg_repair_time
                FROM repair_operations
                {base_where}
            """, *params)

            # Repair type effectiveness
            repair_types = await conn.fetch(f"""
                SELECT
                    repair_type,
                    COUNT(*) as total,
                    COUNT(*) FILTER (WHERE status = 'completed') as successful,
                    AVG(EXTRACT(EPOCH FROM (completed_at::timestamp - created_at::timestamp))) as avg_duration
                FROM repair_operations
                {base_where}
                GROUP BY repair_type
                ORDER BY successful DESC
            """, *params)

            # Daily repair trends
            daily_trends = await conn.fetch(f"""
                SELECT
                    DATE(created_at) as date,
                    COUNT(*) as repairs_initiated,
                    COUNT(*) FILTER (WHERE status = 'completed') as repairs_completed
                FROM repair_operations
                {base_where}
                GROUP BY DATE(created_at)
                ORDER BY date
            """, *params)

            # Priority distribution
            priority_dist = await conn.fetch(f"""
                SELECT priority, COUNT(*) as count
                FROM repair_operations
                {base_where}
                GROUP BY priority
                ORDER BY priority
            """, *params)

            return {
                "time_window_days": days,
                "analysis_scope": "agent_specific" if agent_id else "global",
                "target_agent": agent_id,
                "overall_statistics": dict(overall_stats) if overall_stats else {},
                "repair_type_effectiveness": [dict(row) for row in repair_types],
                "daily_trends": [dict(row) for row in daily_trends],
                "priority_distribution": [{"priority": row["priority"], "count": row["count"]} for row in priority_dist]
            }

    async def get_strategy_performance(self, strategy_name: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get performance metrics for repair strategies"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            if strategy_name:
                query = "SELECT * FROM repair_strategies WHERE strategy_name = $1"
                params = [strategy_name]
            else:
                query = "SELECT * FROM repair_strategies ORDER BY success_rate DESC"
                params = []

            rows = await conn.fetch(query, *params)
            results = []
            for row in rows:
                result = dict(row)
                if result.get("strategy_config"):
                    try:
                        result["strategy_config"] = json.loads(result["strategy_config"])
                    except json.JSONDecodeError:
                        result["strategy_config"] = {}
                results.append(result)
            return results

    # === RECOVERY METRICS ===

    async def track_recovery_metrics(
        self,
        agent_id: str,
        failure_time: str,
        recovery_time: str,
        recovery_method: str,
        downtime_seconds: float,
        data_lost: bool = False
    ) -> str:
        """Track agent recovery metrics"""
        metric_id = str(uuid4())

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO recovery_metrics (
                    metric_id, agent_id, failure_time, recovery_time, recovery_method,
                    downtime_seconds, data_lost, recorded_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
                metric_id, agent_id, failure_time, recovery_time, recovery_method,
                downtime_seconds, data_lost, datetime.now(timezone.utc).isoformat()
            )

        return metric_id

    async def get_recovery_metrics(
        self,
        agent_id: Optional[str] = None,
        days: int = 30
    ) -> Dict[str, Any]:
        """Get recovery time and reliability metrics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.pool.acquire() as conn:
            base_where = "WHERE recorded_at >= $1"
            params = [cutoff_time]

            if agent_id:
                base_where += " AND agent_id = $2"
                params.append(agent_id)

            # Recovery statistics
            recovery_stats = await conn.fetchrow(f"""
                SELECT
                    COUNT(*) as total_recoveries,
                    AVG(downtime_seconds) as avg_downtime_seconds,
                    MIN(downtime_seconds) as min_downtime_seconds,
                    MAX(downtime_seconds) as max_downtime_seconds,
                    COUNT(*) FILTER (WHERE data_lost = true) as recoveries_with_data_loss,
                    COUNT(*) FILTER (WHERE downtime_seconds <= 60) as fast_recoveries
                FROM recovery_metrics
                {base_where}
            """, *params)

            # Recovery methods effectiveness
            recovery_methods = await conn.fetch(f"""
                SELECT
                    recovery_method,
                    COUNT(*) as count,
                    AVG(downtime_seconds) as avg_downtime,
                    COUNT(*) FILTER (WHERE data_lost = false) as successful_no_data_loss
                FROM recovery_metrics
                {base_where}
                GROUP BY recovery_method
                ORDER BY avg_downtime ASC
            """, *params)

            return {
                "time_window_days": days,
                "analysis_scope": "agent_specific" if agent_id else "global",
                "target_agent": agent_id,
                "recovery_statistics": dict(recovery_stats) if recovery_stats else {},
                "recovery_methods": [dict(row) for row in recovery_methods]
            }

    # === MAINTENANCE ===

    async def cleanup_old_data(self, days: int = 90) -> Dict[str, int]:
        """Clean up old repair-related data"""
        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.transaction() as conn:
            # Clean old repair operations
            repairs_result = await conn.execute(
                "DELETE FROM repair_operations WHERE created_at < $1 AND status IN ('completed', 'failed', 'cancelled')",
                cutoff_time
            )
            repairs_deleted = int(repairs_result.split()[-1]) if repairs_result.split()[-1].isdigit() else 0

            # Clean old error patterns
            errors_result = await conn.execute(
                "DELETE FROM error_patterns WHERE occurred_at < $1", cutoff_time
            )
            errors_deleted = int(errors_result.split()[-1]) if errors_result.split()[-1].isdigit() else 0

            # Clean old recovery metrics
            metrics_result = await conn.execute(
                "DELETE FROM recovery_metrics WHERE recorded_at < $1", cutoff_time
            )
            metrics_deleted = int(metrics_result.split()[-1]) if metrics_result.split()[-1].isdigit() else 0

            return {
                "repair_operations_deleted": repairs_deleted,
                "error_patterns_deleted": errors_deleted,
                "recovery_metrics_deleted": metrics_deleted
            }

    async def get_health_status(self) -> Dict[str, Any]:
        """Get database health status"""
        if not self.pool:
            return {"status": "disconnected", "pool": None}

        try:
            async with self.pool.acquire() as conn:
                # Test connectivity
                result = await conn.fetchval("SELECT 1")

                # Get table counts
                repairs_count = await conn.fetchval("SELECT COUNT(*) FROM repair_operations")
                strategies_count = await conn.fetchval("SELECT COUNT(*) FROM repair_strategies")
                auto_repair_configs = await conn.fetchval("SELECT COUNT(*) FROM auto_repair_config")
                error_patterns = await conn.fetchval("SELECT COUNT(*) FROM error_patterns")
                recovery_metrics = await conn.fetchval("SELECT COUNT(*) FROM recovery_metrics")

                # Get recent activity
                recent_repairs = await conn.fetchval(
                    "SELECT COUNT(*) FROM repair_operations WHERE created_at >= $1",
                    (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                )

                pending_repairs = await conn.fetchval(
                    "SELECT COUNT(*) FROM repair_operations WHERE status = 'pending'"
                )

                # Success rate
                success_rate = await conn.fetchval("""
                    SELECT
                        CASE
                            WHEN COUNT(*) = 0 THEN 0
                            ELSE (COUNT(*) FILTER (WHERE status = 'completed') * 100.0 / COUNT(*))
                        END
                    FROM repair_operations
                    WHERE created_at >= $1
                """, (datetime.now(timezone.utc) - timedelta(days=7)).isoformat())

                return {
                    "status": "healthy" if result == 1 else "unhealthy",
                    "pool_size": self.pool.get_size(),
                    "repair_operations": repairs_count,
                    "repair_strategies": strategies_count,
                    "auto_repair_configs": auto_repair_configs,
                    "error_patterns": error_patterns,
                    "recovery_metrics": recovery_metrics,
                    "recent_repairs_24h": recent_repairs,
                    "pending_repairs": pending_repairs,
                    "success_rate_7d": float(success_rate) if success_rate else 0.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }


# Global store instance
repair_store: Optional[PostgreSQLRepairStore] = None


async def get_repair_store() -> PostgreSQLRepairStore:
    """Get the global repair store instance"""
    global repair_store
    if repair_store is None:
        raise RuntimeError("Repair store not initialized")
    return repair_store


async def initialize_repair_store(database_url: str):
    """Initialize the global repair store"""
    global repair_store
    repair_store = PostgreSQLRepairStore(database_url)
    await repair_store.initialize()


async def close_repair_store():
    """Close the global repair store"""
    global repair_store
    if repair_store:
        await repair_store.close()
        repair_store = None