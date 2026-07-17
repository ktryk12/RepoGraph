"""
Truth Migration Service

Handles migration from shared databases to consolidated truth service database:
- shared/babyai_shared/truth/facts.sqlite → truth.db/facts
- shared/babyai_shared/truth/proposals.sqlite → truth.db/proposals
- libs/truth/facts.sqlite → truth.db/facts (merge)
- libs/truth/proposals.sqlite → truth.db/proposals (merge)
"""

import asyncio
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from uuid import uuid4

logger = logging.getLogger(__name__)

class TruthMigrationService:
    """Handles migration from legacy truth databases."""

    def __init__(self, truth_database):
        self.truth_database = truth_database
        self.repo_root = Path(__file__).parent.parent.parent.parent

        # Source database paths
        self.source_databases = {
            "shared_facts": self.repo_root / "shared/babyai_shared/truth/facts.sqlite",
            "shared_proposals": self.repo_root / "shared/babyai_shared/truth/proposals.sqlite",
            "libs_facts": self.repo_root / "libs/truth/facts.sqlite",
            "libs_proposals": self.repo_root / "libs/truth/proposals.sqlite"
        }

    async def migration_needed(self) -> bool:
        """Check if migration is needed."""
        try:
            # Check if any source databases exist
            for name, path in self.source_databases.items():
                if path.exists():
                    # Check if this database has been migrated
                    migrated = await self._check_migration_status(str(path))
                    if not migrated:
                        logger.info(f"Migration needed for {name}: {path}")
                        return True

            return False

        except Exception as e:
            logger.error(f"Error checking migration status: {e}")
            return False

    async def _check_migration_status(self, source_path: str) -> bool:
        """Check if a source database has been migrated."""
        try:
            async with self.truth_database.get_connection() as conn:
                cursor = await conn.execute(
                    "SELECT COUNT(*) FROM truth_migration_log WHERE source_database = ? AND status = 'completed'",
                    (source_path,)
                )
                result = await cursor.fetchone()
                return result[0] > 0

        except Exception as e:
            logger.error(f"Error checking migration status for {source_path}: {e}")
            return False

    async def migrate_all_sources(self):
        """Migrate all source databases."""
        logger.info("Starting migration of all source databases")

        migration_results = {}

        for name, path in self.source_databases.items():
            if not path.exists():
                logger.info(f"Source database {name} not found: {path}")
                continue

            try:
                if await self._check_migration_status(str(path)):
                    logger.info(f"Database {name} already migrated, skipping")
                    continue

                logger.info(f"Migrating {name} from {path}")

                if "facts" in name:
                    result = await self._migrate_facts_database(str(path))
                else:
                    result = await self._migrate_proposals_database(str(path))

                migration_results[name] = result
                logger.info(f"Migration completed for {name}: {result}")

            except Exception as e:
                logger.error(f"Failed to migrate {name}: {e}")
                migration_results[name] = {"status": "failed", "error": str(e)}

        return migration_results

    async def _migrate_facts_database(self, source_path: str) -> Dict[str, Any]:
        """Migrate a facts database."""
        migration_id = str(uuid4())

        # Start migration log
        await self._start_migration_log(migration_id, source_path, "facts")

        try:
            # Connect to source database
            source_conn = sqlite3.connect(source_path)
            source_conn.row_factory = sqlite3.Row

            # Get source schema and data
            schema_info = await self._analyze_source_facts_schema(source_conn)
            facts_data = await self._extract_facts_data(source_conn)

            source_conn.close()

            # Migrate data to truth database
            migrated_count = await self._import_facts_data(facts_data, source_path)

            # Complete migration log
            await self._complete_migration_log(
                migration_id,
                "completed",
                len(facts_data),
                migrated_count,
                0
            )

            return {
                "status": "completed",
                "records_processed": len(facts_data),
                "records_migrated": migrated_count,
                "schema_info": schema_info
            }

        except Exception as e:
            await self._complete_migration_log(
                migration_id,
                "failed",
                0, 0, 1,
                error_details=str(e)
            )
            raise

    async def _migrate_proposals_database(self, source_path: str) -> Dict[str, Any]:
        """Migrate a proposals database."""
        migration_id = str(uuid4())

        # Start migration log
        await self._start_migration_log(migration_id, source_path, "proposals")

        try:
            # Connect to source database
            source_conn = sqlite3.connect(source_path)
            source_conn.row_factory = sqlite3.Row

            # Get source schema and data
            schema_info = await self._analyze_source_proposals_schema(source_conn)
            proposals_data = await self._extract_proposals_data(source_conn)

            source_conn.close()

            # Migrate data to truth database
            migrated_count = await self._import_proposals_data(proposals_data, source_path)

            # Complete migration log
            await self._complete_migration_log(
                migration_id,
                "completed",
                len(proposals_data),
                migrated_count,
                0
            )

            return {
                "status": "completed",
                "records_processed": len(proposals_data),
                "records_migrated": migrated_count,
                "schema_info": schema_info
            }

        except Exception as e:
            await self._complete_migration_log(
                migration_id,
                "failed",
                0, 0, 1,
                error_details=str(e)
            )
            raise

    async def _analyze_source_facts_schema(self, source_conn) -> Dict[str, Any]:
        """Analyze source facts database schema."""
        try:
            # Get table info
            cursor = source_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            schema_info = {"tables": tables, "columns": {}}

            for table in tables:
                cursor = source_conn.execute(f"PRAGMA table_info({table})")
                columns = [{"name": row[1], "type": row[2], "notnull": row[3], "pk": row[5]}
                          for row in cursor.fetchall()]
                schema_info["columns"][table] = columns

            return schema_info

        except Exception as e:
            logger.error(f"Error analyzing source facts schema: {e}")
            return {"error": str(e)}

    async def _analyze_source_proposals_schema(self, source_conn) -> Dict[str, Any]:
        """Analyze source proposals database schema."""
        try:
            # Get table info
            cursor = source_conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]

            schema_info = {"tables": tables, "columns": {}}

            for table in tables:
                cursor = source_conn.execute(f"PRAGMA table_info({table})")
                columns = [{"name": row[1], "type": row[2], "notnull": row[3], "pk": row[5]}
                          for row in cursor.fetchall()]
                schema_info["columns"][table] = columns

            return schema_info

        except Exception as e:
            logger.error(f"Error analyzing source proposals schema: {e}")
            return {"error": str(e)}

    async def _extract_facts_data(self, source_conn) -> List[Dict[str, Any]]:
        """Extract facts data from source database."""
        facts = []

        try:
            # Try common table names for facts
            table_names = ["facts", "truth_facts", "fact", "truths"]

            for table_name in table_names:
                try:
                    cursor = source_conn.execute(f"SELECT * FROM {table_name}")
                    rows = cursor.fetchall()

                    for row in rows:
                        fact = dict(row)
                        facts.append(fact)

                    if facts:  # Found data in this table
                        logger.info(f"Extracted {len(facts)} facts from table {table_name}")
                        break

                except sqlite3.OperationalError:
                    continue  # Table doesn't exist

            return facts

        except Exception as e:
            logger.error(f"Error extracting facts data: {e}")
            return []

    async def _extract_proposals_data(self, source_conn) -> List[Dict[str, Any]]:
        """Extract proposals data from source database."""
        proposals = []

        try:
            # Try common table names for proposals
            table_names = ["proposals", "truth_proposals", "proposal", "fact_proposals"]

            for table_name in table_names:
                try:
                    cursor = source_conn.execute(f"SELECT * FROM {table_name}")
                    rows = cursor.fetchall()

                    for row in rows:
                        proposal = dict(row)
                        proposals.append(proposal)

                    if proposals:  # Found data in this table
                        logger.info(f"Extracted {len(proposals)} proposals from table {table_name}")
                        break

                except sqlite3.OperationalError:
                    continue  # Table doesn't exist

            return proposals

        except Exception as e:
            logger.error(f"Error extracting proposals data: {e}")
            return []

    async def _import_facts_data(self, facts_data: List[Dict], source_path: str) -> int:
        """Import facts data into truth database."""
        migrated_count = 0

        async with self.truth_database.get_connection() as conn:
            for fact in facts_data:
                try:
                    # Generate new ID if needed
                    fact_id = fact.get("id", fact.get("fact_id", str(uuid4())))

                    # Map source fields to target schema
                    normalized_fact = await self._normalize_fact_data(fact, source_path)

                    # Check for duplicates
                    if await self._fact_exists(conn, normalized_fact):
                        logger.debug(f"Fact already exists, skipping: {fact_id}")
                        continue

                    # Insert fact
                    await conn.execute("""
                        INSERT INTO truth_facts (
                            fact_id, fact_content, fact_type, confidence,
                            source_id, source_type, status, created_at,
                            created_by, tags, metadata, migrated_from, original_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        normalized_fact["fact_id"],
                        normalized_fact["fact_content"],
                        normalized_fact["fact_type"],
                        normalized_fact["confidence"],
                        normalized_fact["source_id"],
                        normalized_fact["source_type"],
                        normalized_fact["status"],
                        normalized_fact["created_at"],
                        normalized_fact["created_by"],
                        normalized_fact["tags"],
                        normalized_fact["metadata"],
                        source_path,
                        str(fact.get("id", fact.get("fact_id", "")))
                    ))

                    migrated_count += 1

                except Exception as e:
                    logger.error(f"Error importing fact {fact}: {e}")
                    continue

            await conn.commit()

        return migrated_count

    async def _import_proposals_data(self, proposals_data: List[Dict], source_path: str) -> int:
        """Import proposals data into truth database."""
        migrated_count = 0

        async with self.truth_database.get_connection() as conn:
            for proposal in proposals_data:
                try:
                    # Generate new ID if needed
                    proposal_id = proposal.get("id", proposal.get("proposal_id", str(uuid4())))

                    # Map source fields to target schema
                    normalized_proposal = await self._normalize_proposal_data(proposal, source_path)

                    # Check for duplicates
                    if await self._proposal_exists(conn, normalized_proposal):
                        logger.debug(f"Proposal already exists, skipping: {proposal_id}")
                        continue

                    # Insert proposal
                    await conn.execute("""
                        INSERT INTO truth_proposals (
                            proposal_id, proposed_fact, proposal_type, justification,
                            target_fact_id, status, submitted_by, submitted_at,
                            metadata, tags, migrated_from, original_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        normalized_proposal["proposal_id"],
                        normalized_proposal["proposed_fact"],
                        normalized_proposal["proposal_type"],
                        normalized_proposal["justification"],
                        normalized_proposal["target_fact_id"],
                        normalized_proposal["status"],
                        normalized_proposal["submitted_by"],
                        normalized_proposal["submitted_at"],
                        normalized_proposal["metadata"],
                        normalized_proposal["tags"],
                        source_path,
                        str(proposal.get("id", proposal.get("proposal_id", "")))
                    ))

                    migrated_count += 1

                except Exception as e:
                    logger.error(f"Error importing proposal {proposal}: {e}")
                    continue

            await conn.commit()

        return migrated_count

    async def _normalize_fact_data(self, fact: Dict, source_path: str) -> Dict[str, Any]:
        """Normalize fact data to target schema."""
        return {
            "fact_id": fact.get("id", fact.get("fact_id", str(uuid4()))),
            "fact_content": fact.get("content", fact.get("fact_content", fact.get("text", ""))),
            "fact_type": fact.get("type", fact.get("fact_type", "assertion")),
            "confidence": float(fact.get("confidence", 1.0)),
            "source_id": fact.get("source", fact.get("source_id", "migration")),
            "source_type": "migration",
            "status": fact.get("status", "active"),
            "created_at": fact.get("created_at", datetime.now().isoformat()),
            "created_by": fact.get("created_by", "migration_service"),
            "tags": json.dumps(fact.get("tags", [])),
            "metadata": json.dumps({
                "migrated_from": source_path,
                "migration_timestamp": datetime.now().isoformat(),
                "original_data": fact
            })
        }

    async def _normalize_proposal_data(self, proposal: Dict, source_path: str) -> Dict[str, Any]:
        """Normalize proposal data to target schema."""
        return {
            "proposal_id": proposal.get("id", proposal.get("proposal_id", str(uuid4()))),
            "proposed_fact": proposal.get("content", proposal.get("proposed_fact", proposal.get("text", ""))),
            "proposal_type": proposal.get("type", proposal.get("proposal_type", "new_fact")),
            "justification": proposal.get("justification", proposal.get("reason", "")),
            "target_fact_id": proposal.get("target_fact_id"),
            "status": proposal.get("status", "pending"),
            "submitted_by": proposal.get("submitted_by", proposal.get("author", "migration")),
            "submitted_at": proposal.get("submitted_at", proposal.get("created_at", datetime.now().isoformat())),
            "metadata": json.dumps({
                "migrated_from": source_path,
                "migration_timestamp": datetime.now().isoformat(),
                "original_data": proposal
            }),
            "tags": json.dumps(proposal.get("tags", []))
        }

    async def _fact_exists(self, conn, fact: Dict) -> bool:
        """Check if fact already exists (to avoid duplicates)."""
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM truth_facts WHERE fact_content = ? AND source_id = ?",
            (fact["fact_content"], fact["source_id"])
        )
        result = await cursor.fetchone()
        return result[0] > 0

    async def _proposal_exists(self, conn, proposal: Dict) -> bool:
        """Check if proposal already exists (to avoid duplicates)."""
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM truth_proposals WHERE proposed_fact = ? AND submitted_by = ?",
            (proposal["proposed_fact"], proposal["submitted_by"])
        )
        result = await cursor.fetchone()
        return result[0] > 0

    async def _start_migration_log(self, migration_id: str, source_path: str, migration_type: str):
        """Start migration log entry."""
        async with self.truth_database.get_connection() as conn:
            await conn.execute("""
                INSERT INTO truth_migration_log (
                    migration_id, source_database, migration_type, status, started_at
                ) VALUES (?, ?, ?, 'started', ?)
            """, (migration_id, source_path, migration_type, datetime.now().isoformat()))
            await conn.commit()

    async def _complete_migration_log(self, migration_id: str, status: str,
                                    records_processed: int, records_migrated: int,
                                    errors_encountered: int, error_details: str = None):
        """Complete migration log entry."""
        async with self.truth_database.get_connection() as conn:
            await conn.execute("""
                UPDATE truth_migration_log
                SET status = ?, records_processed = ?, records_migrated = ?,
                    errors_encountered = ?, completed_at = ?, error_details = ?
                WHERE migration_id = ?
            """, (status, records_processed, records_migrated, errors_encountered,
                  datetime.now().isoformat(), error_details, migration_id))
            await conn.commit()

    async def get_migration_status(self) -> Dict[str, Any]:
        """Get current migration status."""
        try:
            async with self.truth_database.get_connection() as conn:
                cursor = await conn.execute("""
                    SELECT source_database, status, records_processed, records_migrated,
                           errors_encountered, started_at, completed_at
                    FROM truth_migration_log
                    ORDER BY started_at DESC
                """)
                migrations = await cursor.fetchall()

                return {
                    "migrations": [dict(row) for row in migrations],
                    "total_migrations": len(migrations),
                    "completed_migrations": len([m for m in migrations if dict(m)["status"] == "completed"])
                }

        except Exception as e:
            logger.error(f"Error getting migration status: {e}")
            return {"error": str(e)}