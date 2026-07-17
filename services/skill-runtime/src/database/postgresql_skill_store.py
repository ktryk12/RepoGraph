"""
PostgreSQL store for skill-runtime service

Handles skill registry, domain indexing, role-based filtering, and skill lifecycle management.
"""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Set
from enum import Enum

import asyncpg
from asyncpg import Pool

logger = logging.getLogger(__name__)


class SkillSource(str, Enum):
    """Skill source types"""
    LOCAL = "local"
    CODEX = "codex"
    GITHUB = "github"
    HUGGINGFACE = "huggingface"


class PostgreSQLSkillStore:
    """PostgreSQL storage for skill registry management"""

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
            logger.info("PostgreSQL skill store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize skill store: {e}")
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

    # === SKILL MANAGEMENT ===

    async def register_skill(
        self,
        skill_id: str,
        source: str,
        uri: str,
        domains: List[str],
        dimensions: List[str],
        content: str,
        ttl_seconds: int = 3600,
        token_count: int = 0,
        sandboxed: bool = False,
        sandbox_until: Optional[datetime] = None
    ) -> None:
        """Register a new skill or update existing one"""
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)

        async with self.transaction() as conn:
            # Upsert skill record
            await conn.execute("""
                INSERT INTO skills (
                    skill_id, source, uri, domains, dimensions, content,
                    fetched_at, expires_at, ttl_seconds, token_count,
                    sandboxed, sandbox_until, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                ON CONFLICT (skill_id) DO UPDATE SET
                    source = EXCLUDED.source,
                    uri = EXCLUDED.uri,
                    domains = EXCLUDED.domains,
                    dimensions = EXCLUDED.dimensions,
                    content = EXCLUDED.content,
                    fetched_at = EXCLUDED.fetched_at,
                    expires_at = EXCLUDED.expires_at,
                    ttl_seconds = EXCLUDED.ttl_seconds,
                    token_count = EXCLUDED.token_count,
                    sandboxed = EXCLUDED.sandboxed,
                    sandbox_until = EXCLUDED.sandbox_until,
                    updated_at = EXCLUDED.updated_at
            """,
                skill_id, source, uri, json.dumps(domains), json.dumps(dimensions),
                content, now.isoformat(), expires_at.isoformat(), ttl_seconds,
                token_count, sandboxed,
                sandbox_until.isoformat() if sandbox_until else None,
                now.isoformat(), now.isoformat()
            )

            # Update domain index
            await conn.execute("DELETE FROM skill_domain_index WHERE skill_id = $1", skill_id)
            for domain in domains:
                await conn.execute("""
                    INSERT INTO skill_domain_index (skill_id, domain, created_at)
                    VALUES ($1, $2, $3)
                """, skill_id, domain, now.isoformat())

            # Update dimension index
            await conn.execute("DELETE FROM skill_dimension_index WHERE skill_id = $1", skill_id)
            for dimension in dimensions:
                await conn.execute("""
                    INSERT INTO skill_dimension_index (skill_id, dimension, created_at)
                    VALUES ($1, $2, $3)
                """, skill_id, dimension, now.isoformat())

    async def lookup_skills(
        self,
        domain: Optional[str] = None,
        dimension: Optional[str] = None,
        source: Optional[str] = None,
        active_only: bool = True,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Lookup skills with optional filtering"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        query_parts = ["SELECT DISTINCT s.* FROM skills s"]
        join_parts = []
        where_parts = []
        params = []
        param_count = 0

        # Join with domain index if needed
        if domain:
            join_parts.append("INNER JOIN skill_domain_index sdi ON s.skill_id = sdi.skill_id")
            param_count += 1
            where_parts.append(f"sdi.domain = ${param_count}")
            params.append(domain)

        # Join with dimension index if needed
        if dimension:
            join_parts.append("INNER JOIN skill_dimension_index sdmi ON s.skill_id = sdmi.skill_id")
            param_count += 1
            where_parts.append(f"sdmi.dimension = ${param_count}")
            params.append(dimension)

        # Filter by source
        if source:
            param_count += 1
            where_parts.append(f"s.source = ${param_count}")
            params.append(source)

        # Filter active skills (not expired and not sandboxed)
        if active_only:
            param_count += 1
            where_parts.append(f"s.expires_at > ${param_count}")
            params.append(datetime.now(timezone.utc).isoformat())

            # Check sandbox status
            now_iso = datetime.now(timezone.utc).isoformat()
            param_count += 1
            where_parts.append(f"(s.sandboxed = FALSE OR (s.sandbox_until IS NOT NULL AND s.sandbox_until < ${param_count}))")
            params.append(now_iso)

        # Construct full query
        if join_parts:
            query_parts.extend(join_parts)

        if where_parts:
            query_parts.append("WHERE " + " AND ".join(where_parts))

        query_parts.append("ORDER BY s.fetched_at DESC")
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
                for json_field in ["domains", "dimensions"]:
                    if result.get(json_field):
                        try:
                            result[json_field] = json.loads(result[json_field])
                        except json.JSONDecodeError:
                            result[json_field] = []

                # Compute active status
                result["is_active"] = self._is_skill_active(result)
                results.append(result)

            return results

    async def get_skill(self, skill_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific skill by ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM skills WHERE skill_id = $1", skill_id
            )

            if not row:
                return None

            result = dict(row)
            # Parse JSON fields
            for json_field in ["domains", "dimensions"]:
                if result.get(json_field):
                    try:
                        result[json_field] = json.loads(result[json_field])
                    except json.JSONDecodeError:
                        result[json_field] = []

            # Compute active status
            result["is_active"] = self._is_skill_active(result)
            return result

    async def set_skill_sandboxed(self, skill_id: str, hours: int) -> bool:
        """Set a skill as sandboxed for specified hours"""
        sandbox_until = datetime.now(timezone.utc) + timedelta(hours=hours)

        async with self.transaction() as conn:
            result = await conn.execute("""
                UPDATE skills
                SET sandboxed = TRUE, sandbox_until = $2, updated_at = $3
                WHERE skill_id = $1
            """,
                skill_id, sandbox_until.isoformat(),
                datetime.now(timezone.utc).isoformat()
            )

            return "UPDATE 1" in str(result)

    async def unsandbox_skill(self, skill_id: str) -> bool:
        """Remove sandboxing from a skill"""
        async with self.transaction() as conn:
            result = await conn.execute("""
                UPDATE skills
                SET sandboxed = FALSE, sandbox_until = NULL, updated_at = $2
                WHERE skill_id = $1
            """, skill_id, datetime.now(timezone.utc).isoformat())

            return "UPDATE 1" in str(result)

    async def delete_skill(self, skill_id: str) -> bool:
        """Delete a skill and its indexes"""
        async with self.transaction() as conn:
            # Delete from indexes first (foreign key constraints)
            await conn.execute("DELETE FROM skill_domain_index WHERE skill_id = $1", skill_id)
            await conn.execute("DELETE FROM skill_dimension_index WHERE skill_id = $1", skill_id)

            # Delete skill record
            result = await conn.execute("DELETE FROM skills WHERE skill_id = $1", skill_id)
            return "DELETE 1" in str(result)

    async def refresh_skill_ttl(self, skill_id: str, ttl_seconds: int) -> bool:
        """Refresh skill TTL and expiration"""
        new_expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)

        async with self.transaction() as conn:
            result = await conn.execute("""
                UPDATE skills
                SET expires_at = $2, ttl_seconds = $3, updated_at = $4
                WHERE skill_id = $1
            """,
                skill_id, new_expires_at.isoformat(), ttl_seconds,
                datetime.now(timezone.utc).isoformat()
            )

            return "UPDATE 1" in str(result)

    async def cleanup_expired_skills(self) -> int:
        """Remove expired skills"""
        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            # Get expired skill IDs first
            expired_skills = await conn.fetch(
                "SELECT skill_id FROM skills WHERE expires_at <= $1", now
            )

            skill_ids = [row["skill_id"] for row in expired_skills]

            if not skill_ids:
                return 0

            # Delete from indexes
            await conn.execute(
                "DELETE FROM skill_domain_index WHERE skill_id = ANY($1)", skill_ids
            )
            await conn.execute(
                "DELETE FROM skill_dimension_index WHERE skill_id = ANY($1)", skill_ids
            )

            # Delete skills
            result = await conn.execute(
                "DELETE FROM skills WHERE expires_at <= $1", now
            )

            deleted_count = int(result.split()[-1]) if result.split()[-1].isdigit() else 0
            return deleted_count

    # === ROLE-BASED FILTERING ===

    async def get_skills_for_role(self, role: str, domain: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get skills filtered by role dimensions"""
        # Role to dimension mapping
        role_dimension_map = {
            "Supervisor": ["coordination", "risk", "general"],
            "Architect": ["architecture", "design", "patterns", "general"],
            "Validation": ["testing", "quality", "invariants", "general"],
            "Repair": ["debugging", "antipatterns", "general"],
            "Translator": ["output", "formatting", "conventions", "general"],
        }

        relevant_dimensions = role_dimension_map.get(role, ["general"])

        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        query_parts = ["""
            SELECT DISTINCT s.*
            FROM skills s
            LEFT JOIN skill_dimension_index sdi ON s.skill_id = sdi.skill_id
        """]

        where_parts = []
        params = []
        param_count = 0

        # Filter by domain if specified
        if domain:
            query_parts.append("INNER JOIN skill_domain_index sdom ON s.skill_id = sdom.skill_id")
            param_count += 1
            where_parts.append(f"sdom.domain = ${param_count}")
            params.append(domain)

        # Filter by role dimensions (skills with no dimensions or matching dimensions)
        dimension_conditions = ["sdi.dimension IS NULL"]  # Skills with no dimensions
        for dim in relevant_dimensions:
            param_count += 1
            dimension_conditions.append(f"sdi.dimension = ${param_count}")
            params.append(dim)

        where_parts.append(f"({' OR '.join(dimension_conditions)})")

        # Filter active skills
        param_count += 1
        where_parts.append(f"s.expires_at > ${param_count}")
        params.append(datetime.now(timezone.utc).isoformat())

        now_iso = datetime.now(timezone.utc).isoformat()
        param_count += 1
        where_parts.append(f"(s.sandboxed = FALSE OR (s.sandbox_until IS NOT NULL AND s.sandbox_until < ${param_count}))")
        params.append(now_iso)

        query_parts.append("WHERE " + " AND ".join(where_parts))
        query_parts.append("ORDER BY s.fetched_at DESC")
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
                for json_field in ["domains", "dimensions"]:
                    if result.get(json_field):
                        try:
                            result[json_field] = json.loads(result[json_field])
                        except json.JSONDecodeError:
                            result[json_field] = []

                result["is_active"] = self._is_skill_active(result)
                results.append(result)

            return results

    # === ANALYTICS ===

    async def get_skill_statistics(self) -> Dict[str, Any]:
        """Get skill registry statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            # Total skills
            total_skills = await conn.fetchval("SELECT COUNT(*) FROM skills")

            # Active vs expired
            now = datetime.now(timezone.utc).isoformat()
            active_count = await conn.fetchval(
                "SELECT COUNT(*) FROM skills WHERE expires_at > $1", now
            )
            expired_count = total_skills - active_count

            # Skills by source
            source_stats = await conn.fetch("""
                SELECT source, COUNT(*) as count
                FROM skills
                GROUP BY source
                ORDER BY count DESC
            """)

            # Skills by domain (top 10)
            domain_stats = await conn.fetch("""
                SELECT sdi.domain, COUNT(*) as count
                FROM skill_domain_index sdi
                JOIN skills s ON sdi.skill_id = s.skill_id
                WHERE s.expires_at > $1
                GROUP BY sdi.domain
                ORDER BY count DESC
                LIMIT 10
            """, now)

            # Skills by dimension (top 10)
            dimension_stats = await conn.fetch("""
                SELECT sdmi.dimension, COUNT(*) as count
                FROM skill_dimension_index sdmi
                JOIN skills s ON sdmi.skill_id = s.skill_id
                WHERE s.expires_at > $1
                GROUP BY sdmi.dimension
                ORDER BY count DESC
                LIMIT 10
            """, now)

            # Sandboxed skills
            sandboxed_count = await conn.fetchval(
                "SELECT COUNT(*) FROM skills WHERE sandboxed = TRUE"
            )

            # Token count statistics
            token_stats = await conn.fetchrow("""
                SELECT
                    SUM(token_count) as total_tokens,
                    AVG(token_count) as avg_tokens,
                    MAX(token_count) as max_tokens
                FROM skills
                WHERE expires_at > $1
            """, now)

            # Recent activity (last 24 hours)
            day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            recent_registrations = await conn.fetchval("""
                SELECT COUNT(*) FROM skills
                WHERE created_at >= $1
            """, day_ago)

            return {
                "total_skills": total_skills,
                "active_skills": active_count,
                "expired_skills": expired_count,
                "sandboxed_skills": sandboxed_count,
                "recent_registrations": recent_registrations,
                "skills_by_source": {row["source"]: row["count"] for row in source_stats},
                "top_domains": [{"domain": row["domain"], "count": row["count"]} for row in domain_stats],
                "top_dimensions": [{"dimension": row["dimension"], "count": row["count"]} for row in dimension_stats],
                "token_statistics": dict(token_stats) if token_stats else {},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    async def get_domain_index(self) -> Dict[str, List[str]]:
        """Get complete domain to skill_ids mapping"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT sdi.domain, sdi.skill_id
                FROM skill_domain_index sdi
                JOIN skills s ON sdi.skill_id = s.skill_id
                WHERE s.expires_at > $1
                ORDER BY sdi.domain, sdi.skill_id
            """, datetime.now(timezone.utc).isoformat())

            domain_index = {}
            for row in rows:
                domain = row["domain"]
                skill_id = row["skill_id"]
                if domain not in domain_index:
                    domain_index[domain] = []
                domain_index[domain].append(skill_id)

            return domain_index

    async def get_health_status(self) -> Dict[str, Any]:
        """Get database health status"""
        if not self.pool:
            return {"status": "disconnected", "pool": None}

        try:
            async with self.pool.acquire() as conn:
                # Test basic connectivity
                result = await conn.fetchval("SELECT 1")

                # Get table counts
                skills_count = await conn.fetchval("SELECT COUNT(*) FROM skills")
                domain_index_count = await conn.fetchval("SELECT COUNT(*) FROM skill_domain_index")
                dimension_index_count = await conn.fetchval("SELECT COUNT(*) FROM skill_dimension_index")

                # Get active skills count
                now = datetime.now(timezone.utc).isoformat()
                active_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM skills WHERE expires_at > $1", now
                )

                return {
                    "status": "healthy" if result == 1 else "unhealthy",
                    "pool_size": self.pool.get_size(),
                    "total_skills": skills_count,
                    "active_skills": active_count,
                    "domain_index_entries": domain_index_count,
                    "dimension_index_entries": dimension_index_count,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    def _is_skill_active(self, skill_record: Dict[str, Any]) -> bool:
        """Determine if a skill is currently active"""
        now = datetime.now(timezone.utc)

        # Check if expired
        expires_at_str = skill_record.get("expires_at")
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)
            if now >= expires_at:
                return False

        # Check if sandboxed
        if not skill_record.get("sandboxed", False):
            return True

        sandbox_until_str = skill_record.get("sandbox_until")
        if not sandbox_until_str:
            return False  # Permanently sandboxed

        sandbox_until = datetime.fromisoformat(sandbox_until_str)
        return now > sandbox_until  # Active if sandbox period has ended


# Global store instance
skill_store: Optional[PostgreSQLSkillStore] = None


async def get_skill_store() -> PostgreSQLSkillStore:
    """Get the global skill store instance"""
    global skill_store
    if skill_store is None:
        raise RuntimeError("Skill store not initialized")
    return skill_store


async def initialize_skill_store(database_url: str):
    """Initialize the global skill store"""
    global skill_store
    skill_store = PostgreSQLSkillStore(database_url)
    await skill_store.initialize()


async def close_skill_store():
    """Close the global skill store"""
    global skill_store
    if skill_store:
        await skill_store.close()
        skill_store = None