"""
Truth Database - Database Layer for Truth Service

Provides async SQLite interface for truth management operations.
"""

import aiosqlite
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, AsyncGenerator
from uuid import uuid4

logger = logging.getLogger(__name__)

class TruthDatabase:
    """Async database interface for truth service."""

    def __init__(self, database_path: str):
        self.database_path = database_path
        self.schema_path = Path(__file__).parent.parent / "database" / "schema.sql"

    async def initialize(self):
        """Initialize database with schema if needed."""
        # Ensure database directory exists
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.database_path) as conn:
            # Check if database is already initialized
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='truth_facts'"
            )
            result = await cursor.fetchone()

            if not result:
                logger.info("Initializing truth database schema")
                await self._create_schema(conn)
                logger.info("Truth database schema created")
            else:
                logger.info("Truth database already initialized")

    async def _create_schema(self, conn: aiosqlite.Connection):
        """Create database schema from schema.sql."""
        try:
            with open(self.schema_path, 'r', encoding='utf-8') as f:
                schema_sql = f.read()

            # Execute schema creation
            await conn.executescript(schema_sql)
            await conn.commit()

        except Exception as e:
            logger.error(f"Failed to create database schema: {e}")
            raise

    @asynccontextmanager
    async def get_connection(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Get database connection context manager."""
        async with aiosqlite.connect(self.database_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    async def health_check(self) -> bool:
        """Check database health."""
        try:
            async with self.get_connection() as conn:
                cursor = await conn.execute("SELECT 1")
                result = await cursor.fetchone()
                return result[0] == 1
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

    # Fact operations

    async def create_fact(self, fact_data: Dict[str, Any]) -> str:
        """Create a new truth fact."""
        fact_id = fact_data.get("fact_id", str(uuid4()))

        async with self.get_connection() as conn:
            await conn.execute("""
                INSERT INTO truth_facts (
                    fact_id, fact_content, fact_type, confidence,
                    source_id, source_type, evidence_hash, status,
                    version, supersedes_fact_id, created_at, created_by,
                    tags, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
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
                fact_data.get("created_at", datetime.now().isoformat()),
                fact_data.get("created_by", "system"),
                json.dumps(fact_data.get("tags", [])),
                json.dumps(fact_data.get("metadata", {}))
            ))
            await conn.commit()

        logger.info(f"Created fact {fact_id}: {fact_data['fact_content'][:50]}...")
        return fact_id

    async def get_fact(self, fact_id: str) -> Optional[Dict[str, Any]]:
        """Get a fact by ID."""
        async with self.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM truth_facts WHERE fact_id = ?",
                (fact_id,)
            )
            row = await cursor.fetchone()

            if row:
                fact = dict(row)
                # Parse JSON fields
                fact["tags"] = json.loads(fact["tags"]) if fact["tags"] else []
                fact["metadata"] = json.loads(fact["metadata"]) if fact["metadata"] else {}
                return fact

        return None

    async def list_facts(self, limit: int = 100, offset: int = 0,
                        status: str = "active") -> List[Dict[str, Any]]:
        """List facts with pagination."""
        async with self.get_connection() as conn:
            cursor = await conn.execute("""
                SELECT * FROM truth_facts
                WHERE status = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, (status, limit, offset))
            rows = await cursor.fetchall()

            facts = []
            for row in rows:
                fact = dict(row)
                fact["tags"] = json.loads(fact["tags"]) if fact["tags"] else []
                fact["metadata"] = json.loads(fact["metadata"]) if fact["metadata"] else {}
                facts.append(fact)

            return facts

    async def update_fact(self, fact_id: str, updates: Dict[str, Any]) -> bool:
        """Update a fact."""
        if not updates:
            return False

        # Build dynamic update query
        set_clauses = []
        params = []

        for field, value in updates.items():
            if field in ["tags", "metadata"]:
                value = json.dumps(value)
            set_clauses.append(f"{field} = ?")
            params.append(value)

        # Add updated_at
        set_clauses.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(fact_id)

        async with self.get_connection() as conn:
            cursor = await conn.execute(f"""
                UPDATE truth_facts
                SET {', '.join(set_clauses)}
                WHERE fact_id = ?
            """, params)
            await conn.commit()

            return cursor.rowcount > 0

    async def deprecate_fact(self, fact_id: str, reason: str) -> bool:
        """Deprecate a fact."""
        return await self.update_fact(fact_id, {
            "status": "deprecated",
            "metadata": {"deprecation_reason": reason, "deprecated_at": datetime.now().isoformat()}
        })

    # Proposal operations

    async def create_proposal(self, proposal_data: Dict[str, Any]) -> str:
        """Create a new proposal."""
        proposal_id = proposal_data.get("proposal_id", str(uuid4()))

        async with self.get_connection() as conn:
            await conn.execute("""
                INSERT INTO truth_proposals (
                    proposal_id, proposed_fact, proposal_type, justification,
                    evidence_data, target_fact_id, status, priority,
                    submitted_by, submitted_at, expires_at, metadata, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                proposal_id,
                proposal_data["proposed_fact"],
                proposal_data.get("proposal_type", "new_fact"),
                proposal_data.get("justification"),
                json.dumps(proposal_data.get("evidence_data", {})),
                proposal_data.get("target_fact_id"),
                proposal_data.get("status", "pending"),
                proposal_data.get("priority", "normal"),
                proposal_data["submitted_by"],
                proposal_data.get("submitted_at", datetime.now().isoformat()),
                proposal_data.get("expires_at"),
                json.dumps(proposal_data.get("metadata", {})),
                json.dumps(proposal_data.get("tags", []))
            ))
            await conn.commit()

        logger.info(f"Created proposal {proposal_id}: {proposal_data['proposed_fact'][:50]}...")
        return proposal_id

    async def get_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        """Get a proposal by ID."""
        async with self.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT * FROM truth_proposals WHERE proposal_id = ?",
                (proposal_id,)
            )
            row = await cursor.fetchone()

            if row:
                proposal = dict(row)
                # Parse JSON fields
                proposal["evidence_data"] = json.loads(proposal["evidence_data"]) if proposal["evidence_data"] else {}
                proposal["tags"] = json.loads(proposal["tags"]) if proposal["tags"] else []
                proposal["metadata"] = json.loads(proposal["metadata"]) if proposal["metadata"] else {}
                return proposal

        return None

    async def list_proposals(self, status: str = "pending", limit: int = 100,
                           offset: int = 0) -> List[Dict[str, Any]]:
        """List proposals with pagination."""
        async with self.get_connection() as conn:
            cursor = await conn.execute("""
                SELECT * FROM truth_proposals
                WHERE status = ?
                ORDER BY submitted_at DESC
                LIMIT ? OFFSET ?
            """, (status, limit, offset))
            rows = await cursor.fetchall()

            proposals = []
            for row in rows:
                proposal = dict(row)
                proposal["evidence_data"] = json.loads(proposal["evidence_data"]) if proposal["evidence_data"] else {}
                proposal["tags"] = json.loads(proposal["tags"]) if proposal["tags"] else []
                proposal["metadata"] = json.loads(proposal["metadata"]) if proposal["metadata"] else {}
                proposals.append(proposal)

            return proposals

    async def update_proposal_status(self, proposal_id: str, status: str,
                                   reviewer_id: str = None, review_notes: str = None) -> bool:
        """Update proposal status and review information."""
        updates = {
            "status": status,
            "review_started_at": datetime.now().isoformat() if status == "reviewing" else None,
            "completed_at": datetime.now().isoformat() if status in ["approved", "rejected"] else None,
            "reviewer_id": reviewer_id,
            "review_notes": review_notes
        }

        # Remove None values
        updates = {k: v for k, v in updates.items() if v is not None}

        return await self.update_proposal(proposal_id, updates)

    async def update_proposal(self, proposal_id: str, updates: Dict[str, Any]) -> bool:
        """Update a proposal."""
        if not updates:
            return False

        # Build dynamic update query
        set_clauses = []
        params = []

        for field, value in updates.items():
            if field in ["evidence_data", "tags", "metadata"]:
                value = json.dumps(value)
            set_clauses.append(f"{field} = ?")
            params.append(value)

        params.append(proposal_id)

        async with self.get_connection() as conn:
            cursor = await conn.execute(f"""
                UPDATE truth_proposals
                SET {', '.join(set_clauses)}
                WHERE proposal_id = ?
            """, params)
            await conn.commit()

            return cursor.rowcount > 0

    # Relationship operations

    async def create_relationship(self, source_fact_id: str, target_fact_id: str,
                                relationship_type: str, strength: float = 1.0,
                                description: str = None) -> str:
        """Create a relationship between facts."""
        relationship_id = str(uuid4())

        async with self.get_connection() as conn:
            await conn.execute("""
                INSERT INTO truth_relationships (
                    relationship_id, source_fact_id, target_fact_id,
                    relationship_type, strength, description,
                    created_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                relationship_id, source_fact_id, target_fact_id,
                relationship_type, strength, description,
                datetime.now().isoformat(), "truth-service"
            ))
            await conn.commit()

        return relationship_id

    async def get_fact_relationships(self, fact_id: str) -> List[Dict[str, Any]]:
        """Get all relationships for a fact."""
        async with self.get_connection() as conn:
            cursor = await conn.execute("""
                SELECT * FROM truth_relationships
                WHERE source_fact_id = ? OR target_fact_id = ?
                ORDER BY created_at DESC
            """, (fact_id, fact_id))
            rows = await cursor.fetchall()

            return [dict(row) for row in rows]

    # Query operations

    async def search_facts(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search facts by content."""
        async with self.get_connection() as conn:
            cursor = await conn.execute("""
                SELECT * FROM truth_facts
                WHERE fact_content LIKE ? AND status = 'active'
                ORDER BY confidence DESC, created_at DESC
                LIMIT ?
            """, (f"%{query}%", limit))
            rows = await cursor.fetchall()

            facts = []
            for row in rows:
                fact = dict(row)
                fact["tags"] = json.loads(fact["tags"]) if fact["tags"] else []
                fact["metadata"] = json.loads(fact["metadata"]) if fact["metadata"] else {}
                facts.append(fact)

            return facts

    async def get_facts_by_type(self, fact_type: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get facts by type."""
        async with self.get_connection() as conn:
            cursor = await conn.execute("""
                SELECT * FROM truth_facts
                WHERE fact_type = ? AND status = 'active'
                ORDER BY confidence DESC, created_at DESC
                LIMIT ?
            """, (fact_type, limit))
            rows = await cursor.fetchall()

            facts = []
            for row in rows:
                fact = dict(row)
                fact["tags"] = json.loads(fact["tags"]) if fact["tags"] else []
                fact["metadata"] = json.loads(fact["metadata"]) if fact["metadata"] else {}
                facts.append(fact)

            return facts

    # Statistics and metrics

    async def get_stats(self) -> Dict[str, Any]:
        """Get truth database statistics."""
        async with self.get_connection() as conn:
            stats = {}

            # Fact statistics
            cursor = await conn.execute(
                "SELECT status, COUNT(*) FROM truth_facts GROUP BY status"
            )
            fact_stats = {row[0]: row[1] for row in await cursor.fetchall()}
            stats["facts"] = fact_stats

            # Proposal statistics
            cursor = await conn.execute(
                "SELECT status, COUNT(*) FROM truth_proposals GROUP BY status"
            )
            proposal_stats = {row[0]: row[1] for row in await cursor.fetchall()}
            stats["proposals"] = proposal_stats

            # Relationship statistics
            cursor = await conn.execute(
                "SELECT COUNT(*) FROM truth_relationships"
            )
            result = await cursor.fetchone()
            stats["relationships"] = result[0]

            return stats

    async def close(self):
        """Close database connections."""
        # aiosqlite connections are closed automatically in context managers
        logger.info("Truth database closed")