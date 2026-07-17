"""
PostgreSQL store for lora-orchestrator service

Handles gap reports, adapter candidates, security evaluations, flow results,
voting records, and training metrics persistence.
"""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Tuple
from uuid import uuid4

import asyncpg
from asyncpg import Pool

logger = logging.getLogger(__name__)


class PostgreSQLLoraStore:
    """PostgreSQL storage for LoRA orchestration data"""

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
            logger.info("PostgreSQL LoRA store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize LoRA store: {e}")
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

    # === GAP REPORTS ===

    async def save_gap_report(
        self,
        gap_id: str,
        domain: str,
        severity: str,
        evidence: List[str],
        status: str = "pending",
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Save or update a gap report"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO gap_reports (
                    gap_id, domain, severity, evidence, status, metadata,
                    created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (gap_id) DO UPDATE SET
                    domain = EXCLUDED.domain,
                    severity = EXCLUDED.severity,
                    evidence = EXCLUDED.evidence,
                    status = EXCLUDED.status,
                    metadata = EXCLUDED.metadata,
                    updated_at = EXCLUDED.updated_at
            """,
                gap_id, domain, severity, json.dumps(evidence),
                status, json.dumps(metadata or {}),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat()
            )

    async def get_gap_report(self, gap_id: str) -> Optional[Dict[str, Any]]:
        """Get a gap report by ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM gap_reports WHERE gap_id = $1", gap_id
            )

            if not row:
                return None

            result = dict(row)
            # Parse JSON fields
            if result.get("evidence"):
                try:
                    result["evidence"] = json.loads(result["evidence"])
                except json.JSONDecodeError:
                    result["evidence"] = []

            if result.get("metadata"):
                try:
                    result["metadata"] = json.loads(result["metadata"])
                except json.JSONDecodeError:
                    result["metadata"] = {}

            return result

    async def get_pending_gaps(self, domain: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get pending gap reports, optionally filtered by domain"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        if domain:
            query = """
                SELECT * FROM gap_reports
                WHERE status = 'pending' AND domain = $1
                ORDER BY
                    CASE severity
                        WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2
                        ELSE 3
                    END,
                    created_at ASC
            """
            params = [domain]
        else:
            query = """
                SELECT * FROM gap_reports
                WHERE status = 'pending'
                ORDER BY
                    CASE severity
                        WHEN 'high' THEN 1
                        WHEN 'medium' THEN 2
                        ELSE 3
                    END,
                    created_at ASC
            """
            params = []

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            results = []
            for row in rows:
                result = dict(row)
                # Parse JSON fields
                if result.get("evidence"):
                    try:
                        result["evidence"] = json.loads(result["evidence"])
                    except json.JSONDecodeError:
                        result["evidence"] = []

                if result.get("metadata"):
                    try:
                        result["metadata"] = json.loads(result["metadata"])
                    except json.JSONDecodeError:
                        result["metadata"] = {}
                results.append(result)
            return results

    async def update_gap_status(self, gap_id: str, status: str) -> bool:
        """Update gap report status"""
        async with self.transaction() as conn:
            result = await conn.execute(
                "UPDATE gap_reports SET status = $2, updated_at = $3 WHERE gap_id = $1",
                gap_id, status, datetime.now(timezone.utc).isoformat()
            )
            return "UPDATE 1" in str(result)

    # === ADAPTER CANDIDATES ===

    async def save_adapter_candidate(
        self,
        candidate_id: str,
        source_url: str,
        license_type: str,
        base_model: str,
        param_count: int,
        last_updated: str,
        file_path: str,
        file_format: str,
        domain: str,
        fetched_at: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Save adapter candidate information"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO adapter_candidates (
                    candidate_id, source_url, license_type, base_model, param_count,
                    last_updated, file_path, file_format, domain, fetched_at, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (candidate_id) DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    license_type = EXCLUDED.license_type,
                    base_model = EXCLUDED.base_model,
                    param_count = EXCLUDED.param_count,
                    last_updated = EXCLUDED.last_updated,
                    file_path = EXCLUDED.file_path,
                    file_format = EXCLUDED.file_format,
                    domain = EXCLUDED.domain,
                    fetched_at = EXCLUDED.fetched_at,
                    metadata = EXCLUDED.metadata
            """,
                candidate_id, source_url, license_type, base_model, param_count,
                last_updated, file_path, file_format, domain,
                fetched_at or datetime.now(timezone.utc).isoformat(),
                json.dumps(metadata or {})
            )

    async def get_adapter_candidates(
        self,
        domain: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get adapter candidates, optionally filtered by domain"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        if domain:
            query = "SELECT * FROM adapter_candidates WHERE domain = $1 ORDER BY param_count DESC LIMIT $2"
            params = [domain, limit]
        else:
            query = "SELECT * FROM adapter_candidates ORDER BY param_count DESC LIMIT $1"
            params = [limit]

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            results = []
            for row in rows:
                result = dict(row)
                if result.get("metadata"):
                    try:
                        result["metadata"] = json.loads(result["metadata"])
                    except json.JSONDecodeError:
                        result["metadata"] = {}
                results.append(result)
            return results

    # === SECURITY EVALUATIONS ===

    async def save_security_evaluation(
        self,
        evaluation_id: Optional[str],
        candidate_id: str,
        gap_id: str,
        s6_passed: bool,
        s7_passed: bool,
        s8_passed: bool,
        overall_score: float,
        disqualification_reason: Optional[str] = None,
        evaluation_details: Optional[Dict[str, Any]] = None
    ) -> str:
        """Save security evaluation results"""
        eval_id = evaluation_id or str(uuid4())

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO security_evaluations (
                    evaluation_id, candidate_id, gap_id, s6_passed, s7_passed,
                    s8_passed, overall_score, disqualification_reason,
                    evaluation_details, evaluated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (evaluation_id) DO UPDATE SET
                    s6_passed = EXCLUDED.s6_passed,
                    s7_passed = EXCLUDED.s7_passed,
                    s8_passed = EXCLUDED.s8_passed,
                    overall_score = EXCLUDED.overall_score,
                    disqualification_reason = EXCLUDED.disqualification_reason,
                    evaluation_details = EXCLUDED.evaluation_details,
                    evaluated_at = EXCLUDED.evaluated_at
            """,
                eval_id, candidate_id, gap_id, s6_passed, s7_passed, s8_passed,
                overall_score, disqualification_reason,
                json.dumps(evaluation_details or {}),
                datetime.now(timezone.utc).isoformat()
            )
        return eval_id

    async def get_security_evaluations(
        self,
        gap_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        passed_only: bool = False,
        hours: int = 168  # 1 week default
    ) -> List[Dict[str, Any]]:
        """Get security evaluation results with optional filtering"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        query_parts = ["SELECT * FROM security_evaluations WHERE evaluated_at >= $1"]
        params = [cutoff_time]
        param_count = 1

        if gap_id:
            param_count += 1
            query_parts.append(f"AND gap_id = ${param_count}")
            params.append(gap_id)

        if candidate_id:
            param_count += 1
            query_parts.append(f"AND candidate_id = ${param_count}")
            params.append(candidate_id)

        if passed_only:
            query_parts.append("AND s6_passed = true AND s7_passed = true AND s8_passed = true")

        query_parts.append("ORDER BY overall_score DESC, evaluated_at DESC")
        query = " ".join(query_parts)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            results = []
            for row in rows:
                result = dict(row)
                if result.get("evaluation_details"):
                    try:
                        result["evaluation_details"] = json.loads(result["evaluation_details"])
                    except json.JSONDecodeError:
                        result["evaluation_details"] = {}
                results.append(result)
            return results

    # === LORA FLOW RESULTS ===

    async def save_flow_result(
        self,
        gap_id: str,
        outcome: str,  # external_adapter, self_trained, deferred
        adapter_id: Optional[str],
        security_score: float,
        votes: Dict[str, bool],
        warnings: List[str],
        next_evaluation: str,
        processing_duration_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Save LoRA flow result"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO flow_results (
                    id, gap_id, outcome, adapter_id, security_score, votes,
                    warnings, next_evaluation, processing_duration_seconds,
                    metadata, completed_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
                str(uuid4()), gap_id, outcome, adapter_id, security_score,
                json.dumps(votes), json.dumps(warnings), next_evaluation,
                processing_duration_seconds, json.dumps(metadata or {}),
                datetime.now(timezone.utc).isoformat()
            )

    async def get_flow_results(
        self,
        gap_id: Optional[str] = None,
        outcome: Optional[str] = None,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """Get flow results with optional filtering"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        query_parts = ["SELECT * FROM flow_results WHERE completed_at >= $1"]
        params = [cutoff_time]
        param_count = 1

        if gap_id:
            param_count += 1
            query_parts.append(f"AND gap_id = ${param_count}")
            params.append(gap_id)

        if outcome:
            param_count += 1
            query_parts.append(f"AND outcome = ${param_count}")
            params.append(outcome)

        query_parts.append("ORDER BY completed_at DESC")
        query = " ".join(query_parts)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            results = []
            for row in rows:
                result = dict(row)
                # Parse JSON fields
                for json_field in ["votes", "warnings", "metadata"]:
                    if result.get(json_field):
                        try:
                            result[json_field] = json.loads(result[json_field])
                        except json.JSONDecodeError:
                            result[json_field] = {} if json_field == "metadata" or json_field == "votes" else []
                results.append(result)
            return results

    # === VOTING RECORDS ===

    async def save_voting_record(
        self,
        vote_id: Optional[str],
        candidate_id: str,
        gap_id: str,
        voter_decisions: Dict[str, bool],
        final_decision: bool,
        confidence_score: float,
        voting_details: Optional[Dict[str, Any]] = None
    ) -> str:
        """Save voting record for adapter approval"""
        voting_id = vote_id or str(uuid4())

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO voting_records (
                    vote_id, candidate_id, gap_id, voter_decisions, final_decision,
                    confidence_score, voting_details, voted_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
                voting_id, candidate_id, gap_id, json.dumps(voter_decisions),
                final_decision, confidence_score, json.dumps(voting_details or {}),
                datetime.now(timezone.utc).isoformat()
            )
        return voting_id

    async def get_voting_records(
        self,
        gap_id: Optional[str] = None,
        candidate_id: Optional[str] = None,
        approved_only: bool = False,
        days: int = 30
    ) -> List[Dict[str, Any]]:
        """Get voting records with optional filtering"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        query_parts = ["SELECT * FROM voting_records WHERE voted_at >= $1"]
        params = [cutoff_time]
        param_count = 1

        if gap_id:
            param_count += 1
            query_parts.append(f"AND gap_id = ${param_count}")
            params.append(gap_id)

        if candidate_id:
            param_count += 1
            query_parts.append(f"AND candidate_id = ${param_count}")
            params.append(candidate_id)

        if approved_only:
            query_parts.append("AND final_decision = true")

        query_parts.append("ORDER BY voted_at DESC")
        query = " ".join(query_parts)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            results = []
            for row in rows:
                result = dict(row)
                # Parse JSON fields
                for json_field in ["voter_decisions", "voting_details"]:
                    if result.get(json_field):
                        try:
                            result[json_field] = json.loads(result[json_field])
                        except json.JSONDecodeError:
                            result[json_field] = {}
                results.append(result)
            return results

    # === TRAINING METRICS ===

    async def log_training_attempt(
        self,
        training_id: Optional[str],
        gap_id: str,
        domain: str,
        training_examples_count: int,
        success: bool,
        training_duration_seconds: Optional[float] = None,
        final_adapter_id: Optional[str] = None,
        error_message: Optional[str] = None,
        training_config: Optional[Dict[str, Any]] = None,
        performance_metrics: Optional[Dict[str, Any]] = None
    ) -> str:
        """Log self-training attempt"""
        train_id = training_id or str(uuid4())

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO training_attempts (
                    training_id, gap_id, domain, training_examples_count, success,
                    training_duration_seconds, final_adapter_id, error_message,
                    training_config, performance_metrics, started_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
                train_id, gap_id, domain, training_examples_count, success,
                training_duration_seconds, final_adapter_id, error_message,
                json.dumps(training_config or {}),
                json.dumps(performance_metrics or {}),
                datetime.now(timezone.utc).isoformat()
            )
        return train_id

    async def get_training_statistics(self, days: int = 30) -> Dict[str, Any]:
        """Get training attempt statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.pool.acquire() as conn:
            # Overall training stats
            overall_stats = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total_attempts,
                    COUNT(*) FILTER (WHERE success = true) as successful_attempts,
                    AVG(training_duration_seconds) as avg_training_duration,
                    AVG(training_examples_count) as avg_examples_used
                FROM training_attempts
                WHERE started_at >= $1
            """, cutoff_time)

            # Domain breakdown
            domain_stats = await conn.fetch("""
                SELECT
                    domain,
                    COUNT(*) as attempts,
                    COUNT(*) FILTER (WHERE success = true) as successful,
                    AVG(training_duration_seconds) as avg_duration
                FROM training_attempts
                WHERE started_at >= $1
                GROUP BY domain
                ORDER BY attempts DESC
            """, cutoff_time)

            # Recent failures
            recent_failures = await conn.fetch("""
                SELECT domain, error_message, started_at
                FROM training_attempts
                WHERE started_at >= $1 AND success = false
                ORDER BY started_at DESC
                LIMIT 10
            """, cutoff_time)

            return {
                "time_window_days": days,
                "overall": dict(overall_stats) if overall_stats else {},
                "by_domain": [dict(row) for row in domain_stats],
                "recent_failures": [dict(row) for row in recent_failures]
            }

    # === ANALYTICS ===

    async def get_orchestration_analytics(self, days: int = 7) -> Dict[str, Any]:
        """Get comprehensive orchestration analytics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.pool.acquire() as conn:
            # Flow outcomes
            flow_outcomes = await conn.fetch("""
                SELECT outcome, COUNT(*) as count
                FROM flow_results
                WHERE completed_at >= $1
                GROUP BY outcome
                ORDER BY count DESC
            """, cutoff_time)

            # Security gate performance
            security_stats = await conn.fetch("""
                SELECT
                    COUNT(*) as total_evaluations,
                    COUNT(*) FILTER (WHERE s6_passed = true) as s6_passed,
                    COUNT(*) FILTER (WHERE s7_passed = true) as s7_passed,
                    COUNT(*) FILTER (WHERE s8_passed = true) as s8_passed,
                    COUNT(*) FILTER (WHERE s6_passed = true AND s7_passed = true AND s8_passed = true) as all_passed,
                    AVG(overall_score) as avg_security_score
                FROM security_evaluations
                WHERE evaluated_at >= $1
            """, cutoff_time)

            # Domain activity
            domain_activity = await conn.fetch("""
                SELECT
                    domain,
                    COUNT(*) as gap_reports,
                    COUNT(*) FILTER (WHERE status = 'resolved') as resolved_gaps
                FROM gap_reports
                WHERE created_at >= $1
                GROUP BY domain
                ORDER BY gap_reports DESC
            """, cutoff_time)

            # Voting effectiveness
            voting_stats = await conn.fetch("""
                SELECT
                    COUNT(*) as total_votes,
                    COUNT(*) FILTER (WHERE final_decision = true) as approved_votes,
                    AVG(confidence_score) as avg_confidence
                FROM voting_records
                WHERE voted_at >= $1
            """, cutoff_time)

            return {
                "time_window_days": days,
                "flow_outcomes": [{"outcome": row["outcome"], "count": row["count"]} for row in flow_outcomes],
                "security_performance": dict(security_stats[0]) if security_stats else {},
                "domain_activity": [dict(row) for row in domain_activity],
                "voting_effectiveness": dict(voting_stats[0]) if voting_stats else {}
            }

    async def cleanup_old_data(self, days: int = 90) -> Dict[str, int]:
        """Clean up old orchestration data"""
        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.transaction() as conn:
            # Clean old flow results
            flows_result = await conn.execute(
                "DELETE FROM flow_results WHERE completed_at < $1", cutoff_time
            )
            flows_deleted = int(flows_result.split()[-1]) if flows_result.split()[-1].isdigit() else 0

            # Clean old security evaluations
            evals_result = await conn.execute(
                "DELETE FROM security_evaluations WHERE evaluated_at < $1", cutoff_time
            )
            evals_deleted = int(evals_result.split()[-1]) if evals_result.split()[-1].isdigit() else 0

            # Clean old voting records
            votes_result = await conn.execute(
                "DELETE FROM voting_records WHERE voted_at < $1", cutoff_time
            )
            votes_deleted = int(votes_result.split()[-1]) if votes_result.split()[-1].isdigit() else 0

            # Clean old training attempts
            training_result = await conn.execute(
                "DELETE FROM training_attempts WHERE started_at < $1", cutoff_time
            )
            training_deleted = int(training_result.split()[-1]) if training_result.split()[-1].isdigit() else 0

            # Keep gap reports and adapter candidates longer
            old_cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()

            gaps_result = await conn.execute(
                "DELETE FROM gap_reports WHERE created_at < $1 AND status != 'pending'", old_cutoff
            )
            gaps_deleted = int(gaps_result.split()[-1]) if gaps_result.split()[-1].isdigit() else 0

            return {
                "flow_results_deleted": flows_deleted,
                "security_evaluations_deleted": evals_deleted,
                "voting_records_deleted": votes_deleted,
                "training_attempts_deleted": training_deleted,
                "gap_reports_deleted": gaps_deleted
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
                gap_reports = await conn.fetchval("SELECT COUNT(*) FROM gap_reports")
                candidates = await conn.fetchval("SELECT COUNT(*) FROM adapter_candidates")
                evaluations = await conn.fetchval("SELECT COUNT(*) FROM security_evaluations")
                flow_results = await conn.fetchval("SELECT COUNT(*) FROM flow_results")
                voting_records = await conn.fetchval("SELECT COUNT(*) FROM voting_records")
                training_attempts = await conn.fetchval("SELECT COUNT(*) FROM training_attempts")

                # Recent activity
                recent_flows = await conn.fetchval(
                    "SELECT COUNT(*) FROM flow_results WHERE completed_at >= $1",
                    (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                )

                pending_gaps = await conn.fetchval(
                    "SELECT COUNT(*) FROM gap_reports WHERE status = 'pending'"
                )

                return {
                    "status": "healthy" if result == 1 else "unhealthy",
                    "pool_size": self.pool.get_size(),
                    "gap_reports_total": gap_reports,
                    "adapter_candidates": candidates,
                    "security_evaluations": evaluations,
                    "flow_results": flow_results,
                    "voting_records": voting_records,
                    "training_attempts": training_attempts,
                    "recent_flows_24h": recent_flows,
                    "pending_gaps": pending_gaps,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }


# Global store instance
lora_store: Optional[PostgreSQLLoraStore] = None


async def get_lora_store() -> PostgreSQLLoraStore:
    """Get the global LoRA store instance"""
    global lora_store
    if lora_store is None:
        raise RuntimeError("LoRA store not initialized")
    return lora_store


async def initialize_lora_store(database_url: str):
    """Initialize the global LoRA store"""
    global lora_store
    lora_store = PostgreSQLLoraStore(database_url)
    await lora_store.initialize()


async def close_lora_store():
    """Close the global LoRA store"""
    global lora_store
    if lora_store:
        await lora_store.close()
        lora_store = None