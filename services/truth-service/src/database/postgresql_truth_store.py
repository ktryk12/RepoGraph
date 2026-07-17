"""
PostgreSQL store for truth-service

Handles truth facts, proposals, relationships, versioning, evidence tracking,
and review workflows for the truth management system.
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


class PostgreSQLTruthStore:
    """PostgreSQL storage for truth service data"""

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
            logger.info("PostgreSQL truth store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize truth store: {e}")
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

    # === TRUTH FACTS ===

    async def create_fact(self, fact_data: Dict[str, Any]) -> str:
        """Create a new truth fact"""
        fact_id = fact_data.get("fact_id", str(uuid4()))

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO truth_facts (
                    fact_id, fact_content, fact_type, confidence, source_id,
                    source_type, evidence_hash, status, version, supersedes_fact_id,
                    created_by, tags, metadata, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            """,
                fact_id,
                fact_data["fact_content"],
                fact_data.get("fact_type", "assertion"),
                fact_data.get("confidence", 1.0),
                fact_data.get("source_id"),
                fact_data.get("source_type", "system"),
                fact_data.get("evidence_hash"),
                fact_data.get("status", "active"),
                fact_data.get("version", 1),
                fact_data.get("supersedes_fact_id"),
                fact_data["created_by"],
                json.dumps(fact_data.get("tags", [])),
                json.dumps(fact_data.get("metadata", {})),
                fact_data.get("created_at", datetime.now(timezone.utc).isoformat())
            )

        return fact_id

    async def get_fact(self, fact_id: str) -> Optional[Dict[str, Any]]:
        """Get a fact by ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM truth_facts WHERE fact_id = $1", fact_id)

            if not row:
                return None

            result = dict(row)
            # Parse JSON fields
            if result.get("tags"):
                try:
                    result["tags"] = json.loads(result["tags"])
                except json.JSONDecodeError:
                    result["tags"] = []

            if result.get("metadata"):
                try:
                    result["metadata"] = json.loads(result["metadata"])
                except json.JSONDecodeError:
                    result["metadata"] = {}

            return result

    async def list_facts(
        self,
        limit: int = 100,
        offset: int = 0,
        status: str = "active",
        fact_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List facts with pagination and filtering"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        query_parts = ["SELECT * FROM truth_facts WHERE status = $1"]
        params = [status]
        param_count = 1

        if fact_type:
            param_count += 1
            query_parts.append(f"AND fact_type = ${param_count}")
            params.append(fact_type)

        query_parts.append("ORDER BY created_at DESC")
        param_count += 1
        query_parts.append(f"LIMIT ${param_count}")
        params.append(limit)

        param_count += 1
        query_parts.append(f"OFFSET ${param_count}")
        params.append(offset)

        query = " ".join(query_parts)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            results = []
            for row in rows:
                result = dict(row)
                # Parse JSON fields
                if result.get("tags"):
                    try:
                        result["tags"] = json.loads(result["tags"])
                    except json.JSONDecodeError:
                        result["tags"] = []

                if result.get("metadata"):
                    try:
                        result["metadata"] = json.loads(result["metadata"])
                    except json.JSONDecodeError:
                        result["metadata"] = {}

                results.append(result)
            return results

    async def update_fact(self, fact_id: str, updates: Dict[str, Any]) -> bool:
        """Update a fact with optional versioning"""
        set_clauses = []
        params = [fact_id]
        param_count = 1

        for key, value in updates.items():
            if key in ["tags", "metadata"] and isinstance(value, (dict, list)):
                value = json.dumps(value)

            param_count += 1
            set_clauses.append(f"{key} = ${param_count}")
            params.append(value)

        if not set_clauses:
            return False

        # Always update the updated_at timestamp
        param_count += 1
        set_clauses.append(f"updated_at = ${param_count}")
        params.append(datetime.now(timezone.utc).isoformat())

        query = f"UPDATE truth_facts SET {', '.join(set_clauses)} WHERE fact_id = $1"

        async with self.transaction() as conn:
            result = await conn.execute(query, *params)
            return "UPDATE 1" in str(result)

    async def deprecate_fact(self, fact_id: str, reason: str) -> bool:
        """Deprecate a fact"""
        return await self.update_fact(fact_id, {
            "status": "deprecated",
            "deprecation_reason": reason,
            "deprecated_at": datetime.now(timezone.utc).isoformat()
        })

    async def search_facts(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search facts by content using full-text search"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        # Use PostgreSQL full-text search
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT fact_id, fact_content, fact_type, confidence, status,
                       ts_rank(search_vector, to_tsquery($1)) as rank
                FROM truth_facts
                WHERE search_vector @@ to_tsquery($1)
                  AND status = 'active'
                ORDER BY rank DESC, created_at DESC
                LIMIT $2
            """, query, limit)

            results = []
            for row in rows:
                result = dict(row)
                # Get full fact data for each result
                full_fact = await self.get_fact(result["fact_id"])
                if full_fact:
                    full_fact["search_rank"] = result["rank"]
                    results.append(full_fact)
            return results

    # === FACT PROPOSALS ===

    async def create_proposal(self, proposal_data: Dict[str, Any]) -> str:
        """Create a new fact proposal"""
        proposal_id = proposal_data.get("proposal_id", str(uuid4()))

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO fact_proposals (
                    proposal_id, proposal_type, status, priority, proposed_fact,
                    target_fact_id, justification, submitted_by, expires_at,
                    metadata, submitted_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
                proposal_id,
                proposal_data["proposal_type"],
                proposal_data.get("status", "pending"),
                proposal_data.get("priority", "normal"),
                proposal_data["proposed_fact"],
                proposal_data.get("target_fact_id"),
                proposal_data.get("justification"),
                proposal_data["submitted_by"],
                proposal_data.get("expires_at"),
                json.dumps(proposal_data.get("metadata", {})),
                proposal_data.get("submitted_at", datetime.now(timezone.utc).isoformat())
            )

        return proposal_id

    async def get_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """Get a proposal by ID"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM fact_proposals WHERE proposal_id = $1", proposal_id)

            if not row:
                return None

            result = dict(row)
            if result.get("metadata"):
                try:
                    result["metadata"] = json.loads(result["metadata"])
                except json.JSONDecodeError:
                    result["metadata"] = {}

            return result

    async def list_proposals(
        self,
        status: str = "pending",
        limit: int = 100,
        offset: int = 0,
        proposal_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """List proposals with filtering and pagination"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        query_parts = ["SELECT * FROM fact_proposals WHERE status = $1"]
        params = [status]
        param_count = 1

        if proposal_type:
            param_count += 1
            query_parts.append(f"AND proposal_type = ${param_count}")
            params.append(proposal_type)

        query_parts.append("ORDER BY submitted_at DESC")
        param_count += 1
        query_parts.append(f"LIMIT ${param_count}")
        params.append(limit)

        param_count += 1
        query_parts.append(f"OFFSET ${param_count}")
        params.append(offset)

        query = " ".join(query_parts)

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

    async def update_proposal_status(
        self,
        proposal_id: str,
        status: str,
        reviewer_id: str,
        review_notes: Optional[str] = None
    ) -> bool:
        """Update proposal status with review information"""
        updates = {
            "status": status,
            "reviewed_by": reviewer_id,
            "reviewed_at": datetime.now(timezone.utc).isoformat()
        }

        if review_notes:
            updates["review_notes"] = review_notes

        return await self.update_proposal(proposal_id, updates)

    async def update_proposal(self, proposal_id: str, updates: Dict[str, Any]) -> bool:
        """Update a proposal"""
        set_clauses = []
        params = [proposal_id]
        param_count = 1

        for key, value in updates.items():
            if key == "metadata" and isinstance(value, dict):
                value = json.dumps(value)

            param_count += 1
            set_clauses.append(f"{key} = ${param_count}")
            params.append(value)

        if not set_clauses:
            return False

        query = f"UPDATE fact_proposals SET {', '.join(set_clauses)} WHERE proposal_id = $1"

        async with self.transaction() as conn:
            result = await conn.execute(query, *params)
            return "UPDATE 1" in str(result)

    # === FACT RELATIONSHIPS ===

    async def create_relationship(
        self,
        source_fact_id: str,
        target_fact_id: str,
        relationship_type: str,
        strength: float = 1.0,
        description: Optional[str] = None
    ) -> str:
        """Create a relationship between facts"""
        relationship_id = str(uuid4())

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO fact_relationships (
                    relationship_id, source_fact_id, target_fact_id, relationship_type,
                    strength, description, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
                relationship_id, source_fact_id, target_fact_id, relationship_type,
                strength, description, datetime.now(timezone.utc).isoformat()
            )

        return relationship_id

    async def get_fact_relationships(self, fact_id: str) -> List[Dict[str, Any]]:
        """Get all relationships for a fact"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM fact_relationships
                WHERE source_fact_id = $1 OR target_fact_id = $1
                ORDER BY strength DESC, created_at DESC
            """, fact_id)

            return [dict(row) for row in rows]

    async def delete_relationship(self, relationship_id: str) -> bool:
        """Delete a fact relationship"""
        async with self.transaction() as conn:
            result = await conn.execute(
                "DELETE FROM fact_relationships WHERE relationship_id = $1",
                relationship_id
            )
            return "DELETE 1" in str(result)

    # === EVIDENCE MANAGEMENT ===

    async def add_evidence(
        self,
        fact_id: str,
        evidence_type: str,
        evidence_content: str,
        evidence_url: Optional[str] = None,
        credibility_score: float = 1.0,
        added_by: str = "system"
    ) -> str:
        """Add evidence for a fact"""
        evidence_id = str(uuid4())

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO fact_evidence (
                    evidence_id, fact_id, evidence_type, evidence_content,
                    evidence_url, credibility_score, added_by, added_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
                evidence_id, fact_id, evidence_type, evidence_content,
                evidence_url, credibility_score, added_by,
                datetime.now(timezone.utc).isoformat()
            )

        return evidence_id

    async def get_fact_evidence(self, fact_id: str) -> List[Dict[str, Any]]:
        """Get all evidence for a fact"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM fact_evidence
                WHERE fact_id = $1
                ORDER BY credibility_score DESC, added_at DESC
            """, fact_id)

            return [dict(row) for row in rows]

    async def update_evidence_credibility(self, evidence_id: str, credibility_score: float) -> bool:
        """Update evidence credibility score"""
        async with self.transaction() as conn:
            result = await conn.execute(
                "UPDATE fact_evidence SET credibility_score = $2 WHERE evidence_id = $1",
                evidence_id, credibility_score
            )
            return "UPDATE 1" in str(result)

    # === FACT HISTORY AND VERSIONING ===

    async def create_fact_version(
        self,
        fact_id: str,
        version_number: int,
        changes: Dict[str, Any],
        changed_by: str
    ) -> str:
        """Create a version record for fact changes"""
        version_id = str(uuid4())

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO fact_versions (
                    version_id, fact_id, version_number, changes, changed_by, changed_at
                ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
                version_id, fact_id, version_number, json.dumps(changes),
                changed_by, datetime.now(timezone.utc).isoformat()
            )

        return version_id

    async def get_fact_history(self, fact_id: str) -> List[Dict[str, Any]]:
        """Get version history for a fact"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM fact_versions
                WHERE fact_id = $1
                ORDER BY version_number DESC
            """, fact_id)

            results = []
            for row in rows:
                result = dict(row)
                if result.get("changes"):
                    try:
                        result["changes"] = json.loads(result["changes"])
                    except json.JSONDecodeError:
                        result["changes"] = {}
                results.append(result)
            return results

    # === REVIEW WORKFLOWS ===

    async def assign_reviewer(self, proposal_id: str, reviewer_id: str) -> bool:
        """Assign a reviewer to a proposal"""
        return await self.update_proposal(proposal_id, {
            "assigned_reviewer": reviewer_id,
            "assigned_at": datetime.now(timezone.utc).isoformat()
        })

    async def get_proposals_for_reviewer(self, reviewer_id: str) -> List[Dict[str, Any]]:
        """Get proposals assigned to a specific reviewer"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM fact_proposals
                WHERE assigned_reviewer = $1 AND status = 'pending'
                ORDER BY priority DESC, submitted_at ASC
            """, reviewer_id)

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

    # === ANALYTICS AND STATISTICS ===

    async def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive truth service statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            # Fact statistics by status
            fact_stats = await conn.fetch("""
                SELECT status, COUNT(*) as count
                FROM truth_facts
                GROUP BY status
            """)

            # Proposal statistics by status
            proposal_stats = await conn.fetch("""
                SELECT status, COUNT(*) as count
                FROM fact_proposals
                GROUP BY status
            """)

            # Fact type distribution
            fact_types = await conn.fetch("""
                SELECT fact_type, COUNT(*) as count
                FROM truth_facts
                WHERE status = 'active'
                GROUP BY fact_type
                ORDER BY count DESC
            """)

            # Recent activity
            recent_activity = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE created_at >= $1) as facts_24h,
                    COUNT(*) FILTER (WHERE created_at >= $2) as facts_7d
                FROM truth_facts
            """,
                (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            )

            # Confidence distribution
            confidence_stats = await conn.fetchrow("""
                SELECT
                    AVG(confidence) as avg_confidence,
                    MIN(confidence) as min_confidence,
                    MAX(confidence) as max_confidence,
                    COUNT(*) FILTER (WHERE confidence >= 0.8) as high_confidence_count
                FROM truth_facts
                WHERE status = 'active'
            """)

            # Relationship count
            relationship_count = await conn.fetchval("SELECT COUNT(*) FROM fact_relationships")

            # Evidence count
            evidence_count = await conn.fetchval("SELECT COUNT(*) FROM fact_evidence")

            return {
                "facts": {row["status"]: row["count"] for row in fact_stats},
                "proposals": {row["status"]: row["count"] for row in proposal_stats},
                "fact_types": [{"type": row["fact_type"], "count": row["count"]} for row in fact_types],
                "recent_activity": dict(recent_activity) if recent_activity else {},
                "confidence_stats": dict(confidence_stats) if confidence_stats else {},
                "relationships": relationship_count,
                "evidence_entries": evidence_count,
                "computed_at": datetime.now(timezone.utc).isoformat()
            }

    async def get_fact_quality_metrics(self, fact_id: str) -> Dict[str, Any]:
        """Get quality metrics for a specific fact"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            # Get fact details
            fact = await self.get_fact(fact_id)
            if not fact:
                return {}

            # Count evidence
            evidence_count = await conn.fetchval(
                "SELECT COUNT(*) FROM fact_evidence WHERE fact_id = $1", fact_id
            )

            # Count relationships
            relationship_count = await conn.fetchval(
                "SELECT COUNT(*) FROM fact_relationships WHERE source_fact_id = $1 OR target_fact_id = $1",
                fact_id
            )

            # Average evidence credibility
            avg_credibility = await conn.fetchval(
                "SELECT AVG(credibility_score) FROM fact_evidence WHERE fact_id = $1",
                fact_id
            )

            # Version count
            version_count = await conn.fetchval(
                "SELECT COUNT(*) FROM fact_versions WHERE fact_id = $1", fact_id
            )

            return {
                "fact_id": fact_id,
                "confidence": fact["confidence"],
                "evidence_count": evidence_count,
                "relationship_count": relationship_count,
                "avg_evidence_credibility": float(avg_credibility) if avg_credibility else 0.0,
                "version_count": version_count,
                "quality_score": self._calculate_quality_score(
                    fact["confidence"], evidence_count, relationship_count,
                    float(avg_credibility) if avg_credibility else 0.0
                )
            }

    def _calculate_quality_score(
        self,
        confidence: float,
        evidence_count: int,
        relationship_count: int,
        avg_credibility: float
    ) -> float:
        """Calculate a composite quality score for a fact"""
        # Simple quality scoring algorithm
        evidence_weight = min(evidence_count * 0.1, 0.3)
        relationship_weight = min(relationship_count * 0.05, 0.2)
        credibility_weight = avg_credibility * 0.3

        quality_score = (
            confidence * 0.5 +
            evidence_weight +
            relationship_weight +
            credibility_weight * 0.2
        )

        return min(max(quality_score, 0.0), 1.0)

    # === MAINTENANCE AND CLEANUP ===

    async def cleanup_old_data(self, days: int = 90) -> Dict[str, int]:
        """Clean up old truth service data"""
        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.transaction() as conn:
            # Clean old processed proposals
            proposals_result = await conn.execute(
                "DELETE FROM fact_proposals WHERE status IN ('approved', 'rejected', 'expired') AND submitted_at < $1",
                cutoff_time
            )
            proposals_deleted = int(proposals_result.split()[-1]) if proposals_result.split()[-1].isdigit() else 0

            # Clean old fact versions (keep recent history)
            versions_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            versions_result = await conn.execute(
                "DELETE FROM fact_versions WHERE changed_at < $1 AND version_number < 10",
                versions_cutoff
            )
            versions_deleted = int(versions_result.split()[-1]) if versions_result.split()[-1].isdigit() else 0

            # Update search vectors for facts that need it
            await conn.execute("""
                UPDATE truth_facts
                SET search_vector = to_tsvector('english', fact_content || ' ' || COALESCE(tags::text, ''))
                WHERE search_vector IS NULL OR updated_at > $1
            """, (datetime.now(timezone.utc) - timedelta(days=1)).isoformat())

            return {
                "proposals_deleted": proposals_deleted,
                "versions_deleted": versions_deleted
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
                facts_count = await conn.fetchval("SELECT COUNT(*) FROM truth_facts")
                proposals_count = await conn.fetchval("SELECT COUNT(*) FROM fact_proposals")
                relationships_count = await conn.fetchval("SELECT COUNT(*) FROM fact_relationships")
                evidence_count = await conn.fetchval("SELECT COUNT(*) FROM fact_evidence")

                # Get recent activity
                recent_facts = await conn.fetchval(
                    "SELECT COUNT(*) FROM truth_facts WHERE created_at >= $1",
                    (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
                )

                pending_proposals = await conn.fetchval(
                    "SELECT COUNT(*) FROM fact_proposals WHERE status = 'pending'"
                )

                # Average confidence
                avg_confidence = await conn.fetchval(
                    "SELECT AVG(confidence) FROM truth_facts WHERE status = 'active'"
                )

                return {
                    "status": "healthy" if result == 1 else "unhealthy",
                    "pool_size": self.pool.get_size(),
                    "truth_facts": facts_count,
                    "fact_proposals": proposals_count,
                    "fact_relationships": relationships_count,
                    "fact_evidence": evidence_count,
                    "recent_facts_24h": recent_facts,
                    "pending_proposals": pending_proposals,
                    "avg_fact_confidence": float(avg_confidence) if avg_confidence else 0.0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }


# Global store instance
truth_store: Optional[PostgreSQLTruthStore] = None


async def get_truth_store() -> PostgreSQLTruthStore:
    """Get the global truth store instance"""
    global truth_store
    if truth_store is None:
        raise RuntimeError("Truth store not initialized")
    return truth_store


async def initialize_truth_store(database_url: str):
    """Initialize the global truth store"""
    global truth_store
    truth_store = PostgreSQLTruthStore(database_url)
    await truth_store.initialize()


async def close_truth_store():
    """Close the global truth store"""
    global truth_store
    if truth_store:
        await truth_store.close()
        truth_store = None