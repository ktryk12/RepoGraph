"""
PostgreSQL store for claim-detector service

Handles detected claims persistence, deduplication, scanner analytics, and platform monitoring.
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


class PostgreSQLClaimStore:
    """PostgreSQL storage for claim detection data"""

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
            logger.info("PostgreSQL claim store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize claim store: {e}")
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

    # === DETECTED CLAIMS PERSISTENCE ===

    async def save_detected_claim(
        self,
        claim_id: str,
        raw_text: str,
        source_url: str,
        platform: str,
        virality_score: float,
        controversy_score: float,
        factcheckability_score: float,
        composite_score: float,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Persist a detected claim"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO detected_claims (
                    claim_id, raw_text, source_url, platform, detected_at,
                    virality_score, controversy_score, factcheckability_score,
                    composite_score, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (claim_id) DO UPDATE SET
                    raw_text = EXCLUDED.raw_text,
                    source_url = EXCLUDED.source_url,
                    virality_score = EXCLUDED.virality_score,
                    controversy_score = EXCLUDED.controversy_score,
                    factcheckability_score = EXCLUDED.factcheckability_score,
                    composite_score = EXCLUDED.composite_score,
                    metadata = EXCLUDED.metadata
            """,
                claim_id, raw_text, source_url, platform,
                datetime.now(timezone.utc).isoformat(),
                virality_score, controversy_score, factcheckability_score,
                composite_score, json.dumps(metadata or {})
            )

    async def get_detected_claims(
        self,
        platform: Optional[str] = None,
        min_score: Optional[float] = None,
        hours: int = 24,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Get detected claims with optional filtering"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        query_parts = ["SELECT * FROM detected_claims WHERE detected_at >= $1"]
        params = [cutoff_time]
        param_count = 1

        if platform:
            param_count += 1
            query_parts.append(f"AND platform = ${param_count}")
            params.append(platform)

        if min_score is not None:
            param_count += 1
            query_parts.append(f"AND composite_score >= ${param_count}")
            params.append(min_score)

        query_parts.append("ORDER BY composite_score DESC, detected_at DESC")
        param_count += 1
        query_parts.append(f"LIMIT ${param_count}")
        params.append(limit)

        query = " ".join(query_parts)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            results = []
            for row in rows:
                result = dict(row)
                # Parse metadata JSON
                if result.get("metadata"):
                    try:
                        result["metadata"] = json.loads(result["metadata"])
                    except json.JSONDecodeError:
                        result["metadata"] = {}
                results.append(result)
            return results

    async def get_claim_by_id(self, claim_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific claim by ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM detected_claims WHERE claim_id = $1", claim_id
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

    # === DEDUPLICATION STORE ===

    async def claim_dedupe_key(self, fingerprint: str, ttl_seconds: int) -> bool:
        """
        Claim a deduplication key with TTL
        Returns True if claim was successful, False if already exists
        """
        ttl = max(1, int(ttl_seconds))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)

        try:
            async with self.transaction() as conn:
                await conn.execute("""
                    INSERT INTO claim_dedupe (fingerprint, expires_at, created_at)
                    VALUES ($1, $2, $3)
                """, fingerprint, expires_at.isoformat(), datetime.now(timezone.utc).isoformat())
                return True

        except asyncpg.UniqueViolationError:
            # Check if expired and try to reclaim
            if not self.pool:
                return False

            async with self.pool.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT expires_at FROM claim_dedupe WHERE fingerprint = $1", fingerprint
                )

                if not existing:
                    return False

                expires_at_str = existing["expires_at"]
                expires_at_dt = datetime.fromisoformat(expires_at_str)

                if expires_at_dt <= datetime.now(timezone.utc):
                    # Expired, try to reclaim
                    async with conn.transaction():
                        result = await conn.execute("""
                            UPDATE claim_dedupe
                            SET expires_at = $2, created_at = $3
                            WHERE fingerprint = $1 AND expires_at <= $3
                        """,
                            fingerprint,
                            (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat(),
                            datetime.now(timezone.utc).isoformat()
                        )
                        return "UPDATE 1" in str(result)

                return False

        except Exception as e:
            logger.error(f"Error claiming dedupe key {fingerprint}: {e}")
            return False

    async def is_duplicate_claim(self, fingerprint: str) -> bool:
        """Check if claim is duplicate"""
        if not self.pool:
            return False

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT expires_at FROM claim_dedupe WHERE fingerprint = $1", fingerprint
            )

            if not row:
                return False

            expires_at = datetime.fromisoformat(row["expires_at"])
            return expires_at > datetime.now(timezone.utc)

    async def cleanup_expired_dedupe_claims(self) -> int:
        """Clean up expired deduplication claims"""
        if not self.pool:
            return 0

        async with self.transaction() as conn:
            result = await conn.execute(
                "DELETE FROM claim_dedupe WHERE expires_at <= $1",
                datetime.now(timezone.utc).isoformat()
            )
            return int(result.split()[-1]) if result.split()[-1].isdigit() else 0

    # === SCANNER STATISTICS ===

    async def log_scanner_run(
        self,
        scanner_id: str,
        platform: str,
        candidates_found: int,
        claims_emitted: int,
        claims_skipped_dup: int,
        claims_skipped_score: int,
        scan_duration_seconds: float,
        error_message: Optional[str] = None,
        scanner_config: Optional[Dict[str, Any]] = None
    ) -> None:
        """Log a scanner run for analytics"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO scanner_runs (
                    id, scanner_id, platform, run_at, candidates_found,
                    claims_emitted, claims_skipped_dup, claims_skipped_score,
                    scan_duration_seconds, error_message, scanner_config
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
                str(uuid4()), scanner_id, platform, datetime.now(timezone.utc).isoformat(),
                candidates_found, claims_emitted, claims_skipped_dup, claims_skipped_score,
                scan_duration_seconds, error_message,
                json.dumps(scanner_config or {})
            )

    async def get_scanner_statistics(
        self,
        platform: Optional[str] = None,
        hours: int = 24
    ) -> Dict[str, Any]:
        """Get scanner performance statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self.pool.acquire() as conn:
            # Overall stats
            overall_stats = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total_runs,
                    SUM(candidates_found) as total_candidates,
                    SUM(claims_emitted) as total_emitted,
                    SUM(claims_skipped_dup) as total_skipped_dup,
                    SUM(claims_skipped_score) as total_skipped_score,
                    AVG(scan_duration_seconds) as avg_scan_duration,
                    COUNT(*) FILTER (WHERE error_message IS NOT NULL) as error_count
                FROM scanner_runs
                WHERE run_at >= $1
                  AND ($2::text IS NULL OR platform = $2)
            """, cutoff_time, platform)

            # Platform breakdown
            platform_stats = await conn.fetch("""
                SELECT
                    platform,
                    COUNT(*) as runs,
                    SUM(candidates_found) as candidates,
                    SUM(claims_emitted) as emitted,
                    AVG(scan_duration_seconds) as avg_duration,
                    COUNT(*) FILTER (WHERE error_message IS NOT NULL) as errors
                FROM scanner_runs
                WHERE run_at >= $1
                GROUP BY platform
                ORDER BY emitted DESC
            """, cutoff_time)

            # Recent errors
            recent_errors = await conn.fetch("""
                SELECT platform, error_message, run_at
                FROM scanner_runs
                WHERE run_at >= $1 AND error_message IS NOT NULL
                ORDER BY run_at DESC
                LIMIT 10
            """, cutoff_time)

            return {
                "time_window_hours": hours,
                "overall": dict(overall_stats) if overall_stats else {},
                "by_platform": [dict(row) for row in platform_stats],
                "recent_errors": [dict(row) for row in recent_errors]
            }

    # === PLATFORM MONITORING ===

    async def log_platform_health(
        self,
        platform: str,
        status: str,  # "healthy", "degraded", "error"
        response_time_ms: Optional[float] = None,
        rate_limit_remaining: Optional[int] = None,
        rate_limit_reset_at: Optional[str] = None,
        error_details: Optional[str] = None,
        api_quota_used: Optional[int] = None,
        api_quota_limit: Optional[int] = None
    ) -> None:
        """Log platform health metrics"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO platform_health (
                    id, platform, timestamp, status, response_time_ms,
                    rate_limit_remaining, rate_limit_reset_at, error_details,
                    api_quota_used, api_quota_limit
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            """,
                str(uuid4()), platform, datetime.now(timezone.utc).isoformat(),
                status, response_time_ms, rate_limit_remaining,
                rate_limit_reset_at, error_details, api_quota_used, api_quota_limit
            )

    async def get_platform_health_status(self, hours: int = 1) -> Dict[str, Any]:
        """Get current platform health status"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self.pool.acquire() as conn:
            # Latest status per platform
            latest_status = await conn.fetch("""
                SELECT DISTINCT ON (platform)
                    platform, timestamp, status, response_time_ms,
                    rate_limit_remaining, error_details
                FROM platform_health
                WHERE timestamp >= $1
                ORDER BY platform, timestamp DESC
            """, cutoff_time)

            # Health trends
            health_trends = await conn.fetch("""
                SELECT
                    platform,
                    COUNT(*) as total_checks,
                    COUNT(*) FILTER (WHERE status = 'healthy') as healthy_count,
                    COUNT(*) FILTER (WHERE status = 'degraded') as degraded_count,
                    COUNT(*) FILTER (WHERE status = 'error') as error_count,
                    AVG(response_time_ms) as avg_response_time
                FROM platform_health
                WHERE timestamp >= $1
                GROUP BY platform
            """, cutoff_time)

            return {
                "time_window_hours": hours,
                "latest_status": [dict(row) for row in latest_status],
                "health_trends": [dict(row) for row in health_trends]
            }

    # === SCORE ANALYTICS ===

    async def get_score_analytics(self, days: int = 7) -> Dict[str, Any]:
        """Get scoring analytics and effectiveness metrics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.pool.acquire() as conn:
            # Score distribution
            score_distribution = await conn.fetch("""
                SELECT
                    CASE
                        WHEN composite_score >= 0.8 THEN '0.8+'
                        WHEN composite_score >= 0.6 THEN '0.6-0.8'
                        WHEN composite_score >= 0.4 THEN '0.4-0.6'
                        WHEN composite_score >= 0.2 THEN '0.2-0.4'
                        ELSE '0.0-0.2'
                    END as score_range,
                    COUNT(*) as count
                FROM detected_claims
                WHERE detected_at >= $1
                GROUP BY score_range
                ORDER BY score_range
            """, cutoff_time)

            # Platform score comparison
            platform_scores = await conn.fetch("""
                SELECT
                    platform,
                    COUNT(*) as claim_count,
                    AVG(composite_score) as avg_composite,
                    AVG(virality_score) as avg_virality,
                    AVG(controversy_score) as avg_controversy,
                    AVG(factcheckability_score) as avg_factcheckability
                FROM detected_claims
                WHERE detected_at >= $1
                GROUP BY platform
                ORDER BY avg_composite DESC
            """, cutoff_time)

            # Daily trends
            daily_trends = await conn.fetch("""
                SELECT
                    DATE(detected_at) as date,
                    COUNT(*) as claims,
                    AVG(composite_score) as avg_score
                FROM detected_claims
                WHERE detected_at >= $1
                GROUP BY DATE(detected_at)
                ORDER BY date
            """, cutoff_time)

            return {
                "time_window_days": days,
                "score_distribution": [{"range": row["score_range"], "count": row["count"]} for row in score_distribution],
                "platform_comparison": [dict(row) for row in platform_scores],
                "daily_trends": [dict(row) for row in daily_trends]
            }

    async def cleanup_old_data(self, days: int = 30) -> Dict[str, int]:
        """Clean up old claim detection data"""
        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.transaction() as conn:
            # Clean old detected claims
            claims_result = await conn.execute(
                "DELETE FROM detected_claims WHERE detected_at < $1", cutoff_time
            )
            claims_deleted = int(claims_result.split()[-1]) if claims_result.split()[-1].isdigit() else 0

            # Clean old scanner runs
            runs_result = await conn.execute(
                "DELETE FROM scanner_runs WHERE run_at < $1", cutoff_time
            )
            runs_deleted = int(runs_result.split()[-1]) if runs_result.split()[-1].isdigit() else 0

            # Clean old platform health (keep shorter history)
            health_cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            health_result = await conn.execute(
                "DELETE FROM platform_health WHERE timestamp < $1", health_cutoff
            )
            health_deleted = int(health_result.split()[-1]) if health_result.split()[-1].isdigit() else 0

            # Clean expired dedupe claims
            dedupe_deleted = await self.cleanup_expired_dedupe_claims()

            return {
                "detected_claims_deleted": claims_deleted,
                "scanner_runs_deleted": runs_deleted,
                "platform_health_deleted": health_deleted,
                "dedupe_claims_deleted": dedupe_deleted
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
                claims_count = await conn.fetchval("SELECT COUNT(*) FROM detected_claims")
                dedupe_count = await conn.fetchval("SELECT COUNT(*) FROM claim_dedupe")
                scanner_runs_count = await conn.fetchval("SELECT COUNT(*) FROM scanner_runs")
                health_records_count = await conn.fetchval("SELECT COUNT(*) FROM platform_health")

                # Get recent activity
                recent_claims = await conn.fetchval(
                    "SELECT COUNT(*) FROM detected_claims WHERE detected_at >= $1",
                    (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
                )

                return {
                    "status": "healthy" if result == 1 else "unhealthy",
                    "pool_size": self.pool.get_size(),
                    "detected_claims_total": claims_count,
                    "dedupe_entries": dedupe_count,
                    "scanner_runs_logged": scanner_runs_count,
                    "health_records": health_records_count,
                    "claims_last_hour": recent_claims,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }


# Global store instance
claim_store: Optional[PostgreSQLClaimStore] = None


async def get_claim_store() -> PostgreSQLClaimStore:
    """Get the global claim store instance"""
    global claim_store
    if claim_store is None:
        raise RuntimeError("Claim store not initialized")
    return claim_store


async def initialize_claim_store(database_url: str):
    """Initialize the global claim store"""
    global claim_store
    claim_store = PostgreSQLClaimStore(database_url)
    await claim_store.initialize()


async def close_claim_store():
    """Close the global claim store"""
    global claim_store
    if claim_store:
        await claim_store.close()
        claim_store = None