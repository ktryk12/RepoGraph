"""
PostgreSQL store for request-gate service

Handles deduplication and pending approval persistence.
"""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Mapping

import asyncpg
from asyncpg import Pool

logger = logging.getLogger(__name__)


class PostgreSQLRequestStore:
    """PostgreSQL storage for request gate data"""

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
            logger.info("PostgreSQL request store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize request store: {e}")
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

    # === DEDUPLICATION STORE ===

    async def claim_dedupe_key(self, key: str, ttl_seconds: int) -> bool:
        """
        Claim a deduplication key with TTL
        Returns True if claim was successful (key didn't exist), False if already claimed
        """
        ttl = max(1, int(ttl_seconds))
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)

        try:
            async with self.transaction() as conn:
                # Try to insert the key. If it exists and not expired, this will fail
                await conn.execute("""
                    INSERT INTO dedupe_claims (key, expires_at, created_at)
                    VALUES ($1, $2, $3)
                """, key, expires_at.isoformat(), datetime.now(timezone.utc).isoformat())

                return True  # Successfully claimed

        except asyncpg.UniqueViolationError:
            # Key already exists, check if it's expired
            if not self.pool:
                return False

            async with self.pool.acquire() as conn:
                existing = await conn.fetchrow(
                    "SELECT expires_at FROM dedupe_claims WHERE key = $1", key
                )

                if not existing:
                    return False  # Shouldn't happen, but handle gracefully

                expires_at_str = existing["expires_at"]
                expires_at = datetime.fromisoformat(expires_at_str)

                # If expired, try to update the expiration
                if expires_at <= datetime.now(timezone.utc):
                    async with conn.transaction():
                        result = await conn.execute("""
                            UPDATE dedupe_claims
                            SET expires_at = $2, created_at = $3
                            WHERE key = $1 AND expires_at <= $3
                        """,
                            key,
                            (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat(),
                            datetime.now(timezone.utc).isoformat()
                        )
                        # Check if we successfully updated (i.e., it was expired)
                        return "UPDATE 1" in str(result)

                return False  # Still active

        except Exception as e:
            logger.error(f"Error claiming dedupe key {key}: {e}")
            return False

    async def cleanup_expired_dedupe_claims(self) -> int:
        """Clean up expired deduplication claims"""
        if not self.pool:
            return 0

        async with self.transaction() as conn:
            result = await conn.execute(
                "DELETE FROM dedupe_claims WHERE expires_at <= $1",
                datetime.now(timezone.utc).isoformat()
            )
            # Extract number from result like "DELETE 123"
            deleted_count = int(result.split()[-1]) if result.split()[-1].isdigit() else 0
            return deleted_count

    # === PENDING APPROVALS STORE ===

    async def upsert_pending_approval(self, record: Mapping[str, Any]) -> None:
        """Create or update a pending approval record"""
        normalized = self._normalize_approval_record(record)
        decision_id = normalized.get("decision_id")

        if not decision_id:
            raise ValueError("pending approval requires decision_id")
        if not normalized.get("required_policy_fingerprint"):
            raise ValueError("pending approval requires required_policy_fingerprint")

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO pending_approvals (
                    decision_id, context_id, policy_preset, required_policy_fingerprint,
                    explanation, created_at, safety_profile, write_scope, user_prompt,
                    policy_explanation, if_you_change, updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12
                )
                ON CONFLICT (decision_id) DO UPDATE SET
                    context_id = EXCLUDED.context_id,
                    policy_preset = EXCLUDED.policy_preset,
                    required_policy_fingerprint = EXCLUDED.required_policy_fingerprint,
                    explanation = EXCLUDED.explanation,
                    safety_profile = EXCLUDED.safety_profile,
                    write_scope = EXCLUDED.write_scope,
                    user_prompt = EXCLUDED.user_prompt,
                    policy_explanation = EXCLUDED.policy_explanation,
                    if_you_change = EXCLUDED.if_you_change,
                    updated_at = EXCLUDED.updated_at
            """,
                normalized["decision_id"], normalized.get("context_id"),
                normalized.get("policy_preset"), normalized["required_policy_fingerprint"],
                normalized.get("explanation"), normalized.get("created_at"),
                normalized.get("safety_profile"), normalized.get("write_scope"),
                normalized.get("user_prompt"),
                json.dumps(normalized.get("policy_explanation")) if normalized.get("policy_explanation") else None,
                json.dumps(normalized.get("if_you_change")) if normalized.get("if_you_change") else None,
                datetime.now(timezone.utc).isoformat()
            )

    async def get_pending_approval(self, decision_id: str) -> Optional[Dict[str, Any]]:
        """Get a pending approval by decision ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        normalized_id = str(decision_id or "").strip()
        if not normalized_id:
            return None

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pending_approvals WHERE decision_id = $1",
                normalized_id
            )

            if not row:
                return None

            # Convert back to the expected format
            result = dict(row)

            # Parse JSON fields back to objects
            if result.get("policy_explanation"):
                try:
                    result["policy_explanation"] = json.loads(result["policy_explanation"])
                except json.JSONDecodeError:
                    result["policy_explanation"] = None

            if result.get("if_you_change"):
                try:
                    result["if_you_change"] = json.loads(result["if_you_change"])
                except json.JSONDecodeError:
                    result["if_you_change"] = None

            return result

    async def list_pending_approvals(self, limit: int = 200) -> List[Dict[str, Any]]:
        """List pending approvals, ordered by created_at desc"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        max_items = max(1, int(limit))

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM pending_approvals
                ORDER BY created_at DESC
                LIMIT $1
            """, max_items)

            results = []
            for row in rows:
                result = dict(row)

                # Parse JSON fields back to objects
                if result.get("policy_explanation"):
                    try:
                        result["policy_explanation"] = json.loads(result["policy_explanation"])
                    except json.JSONDecodeError:
                        result["policy_explanation"] = None

                if result.get("if_you_change"):
                    try:
                        result["if_you_change"] = json.loads(result["if_you_change"])
                    except json.JSONDecodeError:
                        result["if_you_change"] = None

                results.append(result)

            return results

    async def mark_approval_processed(
        self,
        decision_id: str,
        status: str,
        processed_by: Optional[str] = None,
        reason: Optional[str] = None
    ) -> bool:
        """Mark an approval as processed and remove it from pending approvals"""
        normalized_id = str(decision_id or "").strip()
        if not normalized_id:
            return False

        async with self.transaction() as conn:
            # First, log the processed approval for audit trail
            await conn.execute("""
                INSERT INTO processed_approvals (
                    decision_id, status, processed_by, reason, processed_at,
                    original_context_id, original_policy_fingerprint
                )
                SELECT decision_id, $2, $3, $4, $5, context_id, required_policy_fingerprint
                FROM pending_approvals
                WHERE decision_id = $1
            """,
                normalized_id, status, processed_by, reason,
                datetime.now(timezone.utc).isoformat()
            )

            # Then remove from pending
            result = await conn.execute(
                "DELETE FROM pending_approvals WHERE decision_id = $1",
                normalized_id
            )

            # Check if any rows were deleted
            deleted_count = int(result.split()[-1]) if result.split()[-1].isdigit() else 0
            return deleted_count > 0

    async def get_approval_statistics(self, hours: int = 24) -> Dict[str, Any]:
        """Get approval statistics for the last N hours"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        async with self.pool.acquire() as conn:
            # Pending approvals count
            pending_count = await conn.fetchval(
                "SELECT COUNT(*) FROM pending_approvals"
            )

            # Recent processed approvals
            processed_stats = await conn.fetchrow("""
                SELECT
                    COUNT(*) as total_processed,
                    COUNT(*) FILTER (WHERE status = 'approved') as approved_count,
                    COUNT(*) FILTER (WHERE status = 'denied') as denied_count
                FROM processed_approvals
                WHERE processed_at >= $1
            """, cutoff_time)

            # Top policy presets
            policy_presets = await conn.fetch("""
                SELECT policy_preset, COUNT(*) as count
                FROM pending_approvals
                WHERE policy_preset IS NOT NULL
                GROUP BY policy_preset
                ORDER BY count DESC
                LIMIT 10
            """)

            # Oldest pending approval
            oldest_pending = await conn.fetchrow("""
                SELECT decision_id, created_at
                FROM pending_approvals
                ORDER BY created_at ASC
                LIMIT 1
            """)

            return {
                "time_window_hours": hours,
                "cutoff_time": cutoff_time,
                "pending_approvals": pending_count or 0,
                "processed_approvals": dict(processed_stats) if processed_stats else {},
                "top_policy_presets": [{"preset": row["policy_preset"], "count": row["count"]} for row in policy_presets],
                "oldest_pending": dict(oldest_pending) if oldest_pending else None
            }

    async def cleanup_old_processed_approvals(self, days: int = 30) -> int:
        """Clean up old processed approval records"""
        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.transaction() as conn:
            result = await conn.execute(
                "DELETE FROM processed_approvals WHERE processed_at < $1", cutoff_time
            )
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
                pending_count = await conn.fetchval("SELECT COUNT(*) FROM pending_approvals")
                processed_count = await conn.fetchval("SELECT COUNT(*) FROM processed_approvals")
                dedupe_count = await conn.fetchval("SELECT COUNT(*) FROM dedupe_claims")

                return {
                    "status": "healthy" if result == 1 else "unhealthy",
                    "pool_size": self.pool.get_size(),
                    "pending_approvals": pending_count,
                    "processed_approvals": processed_count,
                    "dedupe_claims": dedupe_count,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    def _normalize_approval_record(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        """Normalize approval record format"""
        payload = dict(record or {})
        decision_id = str(payload.get("decision_id") or "").strip()
        context_id = str(payload.get("context_id") or "").strip() or None
        policy_preset = str(payload.get("policy_preset") or "").strip() or None
        required_fingerprint = str(payload.get("required_policy_fingerprint") or "").strip().lower()
        explanation = str(payload.get("explanation") or "").strip() or None
        created_at = str(payload.get("created_at") or "").strip() or None
        safety_profile = str(payload.get("safety_profile") or "").strip() or None
        write_scope = str(payload.get("write_scope") or "").strip() or None
        user_prompt = str(payload.get("user_prompt") or "").strip() if isinstance(payload.get("user_prompt"), str) else None

        policy_explanation = payload.get("policy_explanation")
        if_you_change = payload.get("if_you_change")

        normalized = {
            "decision_id": decision_id,
            "context_id": context_id,
            "policy_preset": policy_preset,
            "required_policy_fingerprint": required_fingerprint,
            "explanation": explanation,
            "created_at": created_at,
            "safety_profile": safety_profile,
            "write_scope": write_scope,
            "user_prompt": user_prompt,
        }

        # Include complex fields only if they're valid
        if isinstance(policy_explanation, Mapping):
            normalized["policy_explanation"] = dict(policy_explanation)

        if isinstance(if_you_change, list):
            normalized["if_you_change"] = [item for item in if_you_change if isinstance(item, Mapping)]

        return normalized


# Global store instance
request_store: Optional[PostgreSQLRequestStore] = None


async def get_request_store() -> PostgreSQLRequestStore:
    """Get the global request store instance"""
    global request_store
    if request_store is None:
        raise RuntimeError("Request store not initialized")
    return request_store


async def initialize_request_store(database_url: str):
    """Initialize the global request store"""
    global request_store
    request_store = PostgreSQLRequestStore(database_url)
    await request_store.initialize()


async def close_request_store():
    """Close the global request store"""
    global request_store
    if request_store:
        await request_store.close()
        request_store = None