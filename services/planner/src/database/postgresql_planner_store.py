"""
PostgreSQL store for planner service

Handles intent records, ready records, task specifications, decision lifecycle tracking,
policy contracts, and error analytics.
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


class PostgreSQLPlannerStore:
    """PostgreSQL storage for planner service data"""

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
            logger.info("PostgreSQL planner store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize planner store: {e}")
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

    # === INTENT RECORDS ===

    async def save_intent_record(
        self,
        decision_id: str,
        context_id: str,
        policy_preset: str,
        user_prompt: str,
        template_id: str = "auto",
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Save or update an intent record"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO intent_records (
                    decision_id, context_id, policy_preset, user_prompt,
                    template_id, metadata, received_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (decision_id) DO UPDATE SET
                    context_id = EXCLUDED.context_id,
                    policy_preset = EXCLUDED.policy_preset,
                    user_prompt = EXCLUDED.user_prompt,
                    template_id = EXCLUDED.template_id,
                    metadata = EXCLUDED.metadata,
                    received_at = EXCLUDED.received_at
            """,
                decision_id, context_id, policy_preset, user_prompt,
                template_id, json.dumps(metadata or {}),
                datetime.now(timezone.utc).isoformat()
            )

    async def get_intent_record(self, decision_id: str) -> Optional[Dict[str, Any]]:
        """Get intent record by decision ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM intent_records WHERE decision_id = $1", decision_id
            )

            if not row:
                return None

            result = dict(row)
            if result.get("metadata"):
                try:
                    result["metadata"] = json.loads(result["metadata"])
                except json.JSONDecodeError:
                    result["metadata"] = {}

            return result

    # === READY RECORDS ===

    async def save_ready_record(
        self,
        decision_id: str,
        context_id: str,
        policy_preset: str,
        truth_pack_alias: str,
        user_override_ref: str,
        explanation_text: str,
        override_hash: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Save or update a ready record"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO ready_records (
                    decision_id, context_id, policy_preset, truth_pack_alias,
                    user_override_ref, explanation_text, override_hash,
                    metadata, received_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (decision_id) DO UPDATE SET
                    context_id = EXCLUDED.context_id,
                    policy_preset = EXCLUDED.policy_preset,
                    truth_pack_alias = EXCLUDED.truth_pack_alias,
                    user_override_ref = EXCLUDED.user_override_ref,
                    explanation_text = EXCLUDED.explanation_text,
                    override_hash = EXCLUDED.override_hash,
                    metadata = EXCLUDED.metadata,
                    received_at = EXCLUDED.received_at
            """,
                decision_id, context_id, policy_preset, truth_pack_alias,
                user_override_ref, explanation_text, override_hash,
                json.dumps(metadata or {}), datetime.now(timezone.utc).isoformat()
            )

    async def get_ready_record(self, decision_id: str) -> Optional[Dict[str, Any]]:
        """Get ready record by decision ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM ready_records WHERE decision_id = $1", decision_id
            )

            if not row:
                return None

            result = dict(row)
            if result.get("metadata"):
                try:
                    result["metadata"] = json.loads(result["metadata"])
                except json.JSONDecodeError:
                    result["metadata"] = {}

            return result

    # === TASK SPECIFICATIONS ===

    async def save_task_spec(
        self,
        task_id: str,
        decision_id: str,
        task_spec: Dict[str, Any],
        task_ref: str,
        template_id: str = "auto",
        context_id: Optional[str] = None,
        policy_preset: Optional[str] = None
    ) -> None:
        """Save a generated task specification"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO task_specifications (
                    task_id, decision_id, task_spec, task_ref, template_id,
                    context_id, policy_preset, generated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (task_id) DO UPDATE SET
                    decision_id = EXCLUDED.decision_id,
                    task_spec = EXCLUDED.task_spec,
                    task_ref = EXCLUDED.task_ref,
                    template_id = EXCLUDED.template_id,
                    context_id = EXCLUDED.context_id,
                    policy_preset = EXCLUDED.policy_preset,
                    generated_at = EXCLUDED.generated_at
            """,
                task_id, decision_id, json.dumps(task_spec), task_ref,
                template_id, context_id, policy_preset,
                datetime.now(timezone.utc).isoformat()
            )

    async def get_task_spec(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task specification by task ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM task_specifications WHERE task_id = $1", task_id
            )

            if not row:
                return None

            result = dict(row)
            if result.get("task_spec"):
                try:
                    result["task_spec"] = json.loads(result["task_spec"])
                except json.JSONDecodeError:
                    result["task_spec"] = {}

            return result

    async def get_task_specs_by_decision(self, decision_id: str) -> List[Dict[str, Any]]:
        """Get all task specs for a decision"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM task_specifications WHERE decision_id = $1 ORDER BY generated_at DESC",
                decision_id
            )

            results = []
            for row in rows:
                result = dict(row)
                if result.get("task_spec"):
                    try:
                        result["task_spec"] = json.loads(result["task_spec"])
                    except json.JSONDecodeError:
                        result["task_spec"] = {}
                results.append(result)

            return results

    # === DECISION LIFECYCLE TRACKING ===

    async def track_decision_event(
        self,
        decision_id: str,
        event_type: str,  # intent_received, ready_received, task_generated, decision_published, error
        event_data: Optional[Dict[str, Any]] = None,
        processing_duration_ms: Optional[float] = None,
        error_message: Optional[str] = None
    ) -> None:
        """Track decision lifecycle events"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO decision_events (
                    id, decision_id, event_type, event_data, processing_duration_ms,
                    error_message, timestamp
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
                str(uuid4()), decision_id, event_type,
                json.dumps(event_data or {}), processing_duration_ms,
                error_message, datetime.now(timezone.utc).isoformat()
            )

    async def get_decision_lifecycle(self, decision_id: str) -> List[Dict[str, Any]]:
        """Get complete lifecycle for a decision"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM decision_events
                WHERE decision_id = $1
                ORDER BY timestamp ASC
            """, decision_id)

            results = []
            for row in rows:
                result = dict(row)
                if result.get("event_data"):
                    try:
                        result["event_data"] = json.loads(result["event_data"])
                    except json.JSONDecodeError:
                        result["event_data"] = {}
                results.append(result)

            return results

    async def get_incomplete_decisions(self, hours: int = 24) -> List[Dict[str, Any]]:
        """Get decisions that haven't completed within time window"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT decision_id, MIN(timestamp) as started_at,
                       MAX(timestamp) as last_event, COUNT(*) as event_count,
                       array_agg(DISTINCT event_type) as event_types
                FROM decision_events
                WHERE timestamp >= $1
                GROUP BY decision_id
                HAVING NOT ('decision_published' = ANY(array_agg(event_type)))
                   AND NOT ('error' = ANY(array_agg(event_type)))
                ORDER BY started_at ASC
            """, cutoff_time)

            return [dict(row) for row in rows]

    # === POLICY CONTRACT AUDIT ===

    async def log_policy_contract(
        self,
        decision_id: str,
        policy_preset: str,
        template_id: str,
        contract: Dict[str, Any],
        constraints: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log policy contract generation for audit"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO policy_contracts (
                    id, decision_id, policy_preset, template_id, contract,
                    constraints, generated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
                str(uuid4()), decision_id, policy_preset, template_id,
                json.dumps(contract), json.dumps(constraints or {}),
                datetime.now(timezone.utc).isoformat()
            )

    async def get_policy_usage_stats(self, days: int = 7) -> Dict[str, Any]:
        """Get policy preset and template usage statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.pool.acquire() as conn:
            # Policy preset usage
            preset_stats = await conn.fetch("""
                SELECT policy_preset, COUNT(*) as usage_count
                FROM policy_contracts
                WHERE generated_at >= $1
                GROUP BY policy_preset
                ORDER BY usage_count DESC
            """, cutoff_time)

            # Template usage
            template_stats = await conn.fetch("""
                SELECT template_id, COUNT(*) as usage_count
                FROM policy_contracts
                WHERE generated_at >= $1
                GROUP BY template_id
                ORDER BY usage_count DESC
            """, cutoff_time)

            # Constraint patterns
            constraint_stats = await conn.fetch("""
                SELECT
                    (constraints->>'visibility') as visibility,
                    (constraints->>'safety_mode') as safety_mode,
                    COUNT(*) as count
                FROM policy_contracts
                WHERE generated_at >= $1 AND constraints IS NOT NULL
                GROUP BY visibility, safety_mode
                ORDER BY count DESC
            """, cutoff_time)

            return {
                "time_window_days": days,
                "policy_presets": [{"preset": row["policy_preset"], "count": row["usage_count"]} for row in preset_stats],
                "templates": [{"template": row["template_id"], "count": row["usage_count"]} for row in template_stats],
                "constraint_patterns": [dict(row) for row in constraint_stats]
            }

    # === MEMORY CONTEXT TRACKING ===

    async def log_memory_retrieval(
        self,
        decision_id: str,
        scenario: str,
        retrieval_success: bool,
        memories_retrieved: int = 0,
        retrieval_duration_ms: Optional[float] = None,
        error_message: Optional[str] = None,
        memory_context_preview: Optional[str] = None
    ) -> None:
        """Log memory context retrieval attempts"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO memory_retrievals (
                    id, decision_id, scenario, retrieval_success, memories_retrieved,
                    retrieval_duration_ms, error_message, memory_context_preview,
                    attempted_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
                str(uuid4()), decision_id, scenario, retrieval_success,
                memories_retrieved, retrieval_duration_ms, error_message,
                memory_context_preview, datetime.now(timezone.utc).isoformat()
            )

    async def get_memory_stats(self, days: int = 7) -> Dict[str, Any]:
        """Get memory retrieval statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.pool.acquire() as conn:
            # Overall memory stats
            overall_stats = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total_attempts,
                    COUNT(*) FILTER (WHERE retrieval_success = true) as successful_retrievals,
                    AVG(memories_retrieved) FILTER (WHERE retrieval_success = true) as avg_memories_per_success,
                    AVG(retrieval_duration_ms) as avg_retrieval_time
                FROM memory_retrievals
                WHERE attempted_at >= $1
            """, cutoff_time)

            # By scenario
            scenario_stats = await conn.fetch("""
                SELECT
                    scenario,
                    COUNT(*) as attempts,
                    COUNT(*) FILTER (WHERE retrieval_success = true) as successes,
                    AVG(memories_retrieved) FILTER (WHERE retrieval_success = true) as avg_memories
                FROM memory_retrievals
                WHERE attempted_at >= $1
                GROUP BY scenario
                ORDER BY attempts DESC
            """, cutoff_time)

            return {
                "time_window_days": days,
                "overall": dict(overall_stats) if overall_stats else {},
                "by_scenario": [dict(row) for row in scenario_stats]
            }

    # === ERROR ANALYTICS ===

    async def log_dlq_event(
        self,
        reason_code: str,
        message: str,
        payload: Dict[str, Any],
        source_topic: Optional[str] = None,
        decision_id: Optional[str] = None
    ) -> None:
        """Log dead letter queue events"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO dlq_events (
                    id, reason_code, message, payload, source_topic,
                    decision_id, occurred_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
                str(uuid4()), reason_code, message, json.dumps(payload),
                source_topic, decision_id, datetime.now(timezone.utc).isoformat()
            )

    async def get_error_statistics(self, hours: int = 24) -> Dict[str, Any]:
        """Get error and DLQ statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self.pool.acquire() as conn:
            # DLQ events by reason
            dlq_stats = await conn.fetch("""
                SELECT reason_code, COUNT(*) as count
                FROM dlq_events
                WHERE occurred_at >= $1
                GROUP BY reason_code
                ORDER BY count DESC
            """, cutoff_time)

            # Decision event errors
            event_errors = await conn.fetch("""
                SELECT event_type, COUNT(*) as error_count
                FROM decision_events
                WHERE timestamp >= $1 AND error_message IS NOT NULL
                GROUP BY event_type
                ORDER BY error_count DESC
            """, cutoff_time)

            # Recent DLQ events
            recent_dlq = await conn.fetch("""
                SELECT reason_code, message, occurred_at
                FROM dlq_events
                WHERE occurred_at >= $1
                ORDER BY occurred_at DESC
                LIMIT 10
            """, cutoff_time)

            return {
                "time_window_hours": hours,
                "dlq_by_reason": [{"reason": row["reason_code"], "count": row["count"]} for row in dlq_stats],
                "event_errors": [{"event_type": row["event_type"], "count": row["error_count"]} for row in event_errors],
                "recent_dlq_events": [dict(row) for row in recent_dlq]
            }

    # === ANALYTICS ===

    async def get_planner_analytics(self, days: int = 7) -> Dict[str, Any]:
        """Get comprehensive planner analytics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.pool.acquire() as conn:
            # Decision throughput
            throughput_stats = await conn.fetchrow("""
                SELECT
                    COUNT(DISTINCT decision_id) as total_decisions,
                    COUNT(*) FILTER (WHERE event_type = 'intent_received') as intents_received,
                    COUNT(*) FILTER (WHERE event_type = 'ready_received') as ready_received,
                    COUNT(*) FILTER (WHERE event_type = 'task_generated') as tasks_generated,
                    COUNT(*) FILTER (WHERE event_type = 'decision_published') as decisions_published,
                    COUNT(*) FILTER (WHERE event_type = 'error') as errors
                FROM decision_events
                WHERE timestamp >= $1
            """, cutoff_time)

            # Processing times
            processing_times = await conn.fetchrow("""
                SELECT
                    AVG(processing_duration_ms) as avg_processing_time,
                    PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY processing_duration_ms) as p95_processing_time,
                    MAX(processing_duration_ms) as max_processing_time
                FROM decision_events
                WHERE timestamp >= $1 AND processing_duration_ms IS NOT NULL
            """, cutoff_time)

            # Template usage
            template_usage = await conn.fetch("""
                SELECT template_id, COUNT(*) as usage_count
                FROM task_specifications
                WHERE generated_at >= $1
                GROUP BY template_id
                ORDER BY usage_count DESC
            """, cutoff_time)

            # Daily activity
            daily_activity = await conn.fetch("""
                SELECT
                    DATE(timestamp) as date,
                    COUNT(DISTINCT decision_id) as decisions,
                    COUNT(*) as events
                FROM decision_events
                WHERE timestamp >= $1
                GROUP BY DATE(timestamp)
                ORDER BY date
            """, cutoff_time)

            return {
                "time_window_days": days,
                "throughput": dict(throughput_stats) if throughput_stats else {},
                "performance": dict(processing_times) if processing_times else {},
                "template_usage": [{"template": row["template_id"], "count": row["usage_count"]} for row in template_usage],
                "daily_activity": [dict(row) for row in daily_activity]
            }

    async def cleanup_old_data(self, days: int = 30) -> Dict[str, int]:
        """Clean up old planner data"""
        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.transaction() as conn:
            # Clean old decision events
            events_result = await conn.execute(
                "DELETE FROM decision_events WHERE timestamp < $1", cutoff_time
            )
            events_deleted = int(events_result.split()[-1]) if events_result.split()[-1].isdigit() else 0

            # Clean old policy contracts
            contracts_result = await conn.execute(
                "DELETE FROM policy_contracts WHERE generated_at < $1", cutoff_time
            )
            contracts_deleted = int(contracts_result.split()[-1]) if contracts_result.split()[-1].isdigit() else 0

            # Clean old memory retrievals
            memory_result = await conn.execute(
                "DELETE FROM memory_retrievals WHERE attempted_at < $1", cutoff_time
            )
            memory_deleted = int(memory_result.split()[-1]) if memory_result.split()[-1].isdigit() else 0

            # Clean old DLQ events
            dlq_result = await conn.execute(
                "DELETE FROM dlq_events WHERE occurred_at < $1", cutoff_time
            )
            dlq_deleted = int(dlq_result.split()[-1]) if dlq_result.split()[-1].isdigit() else 0

            # Clean old intent/ready/task records
            # Keep these longer for correlation purposes
            old_cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()

            intents_result = await conn.execute(
                "DELETE FROM intent_records WHERE received_at < $1", old_cutoff
            )
            intents_deleted = int(intents_result.split()[-1]) if intents_result.split()[-1].isdigit() else 0

            ready_result = await conn.execute(
                "DELETE FROM ready_records WHERE received_at < $1", old_cutoff
            )
            ready_deleted = int(ready_result.split()[-1]) if ready_result.split()[-1].isdigit() else 0

            tasks_result = await conn.execute(
                "DELETE FROM task_specifications WHERE generated_at < $1", old_cutoff
            )
            tasks_deleted = int(tasks_result.split()[-1]) if tasks_result.split()[-1].isdigit() else 0

            return {
                "decision_events_deleted": events_deleted,
                "policy_contracts_deleted": contracts_deleted,
                "memory_retrievals_deleted": memory_deleted,
                "dlq_events_deleted": dlq_deleted,
                "intent_records_deleted": intents_deleted,
                "ready_records_deleted": ready_deleted,
                "task_specifications_deleted": tasks_deleted
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
                intents = await conn.fetchval("SELECT COUNT(*) FROM intent_records")
                ready_records = await conn.fetchval("SELECT COUNT(*) FROM ready_records")
                tasks = await conn.fetchval("SELECT COUNT(*) FROM task_specifications")
                events = await conn.fetchval("SELECT COUNT(*) FROM decision_events")
                contracts = await conn.fetchval("SELECT COUNT(*) FROM policy_contracts")
                memory_retrievals = await conn.fetchval("SELECT COUNT(*) FROM memory_retrievals")
                dlq_events = await conn.fetchval("SELECT COUNT(*) FROM dlq_events")

                # Recent activity
                recent_decisions = await conn.fetchval(
                    "SELECT COUNT(DISTINCT decision_id) FROM decision_events WHERE timestamp >= $1",
                    (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
                )

                return {
                    "status": "healthy" if result == 1 else "unhealthy",
                    "pool_size": self.pool.get_size(),
                    "intent_records": intents,
                    "ready_records": ready_records,
                    "task_specifications": tasks,
                    "decision_events": events,
                    "policy_contracts": contracts,
                    "memory_retrievals": memory_retrievals,
                    "dlq_events": dlq_events,
                    "recent_decisions_1h": recent_decisions,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }


# Global store instance
planner_store: Optional[PostgreSQLPlannerStore] = None


async def get_planner_store() -> PostgreSQLPlannerStore:
    """Get the global planner store instance"""
    global planner_store
    if planner_store is None:
        raise RuntimeError("Planner store not initialized")
    return planner_store


async def initialize_planner_store(database_url: str):
    """Initialize the global planner store"""
    global planner_store
    planner_store = PostgreSQLPlannerStore(database_url)
    await planner_store.initialize()


async def close_planner_store():
    """Close the global planner store"""
    global planner_store
    if planner_store:
        await planner_store.close()
        planner_store = None