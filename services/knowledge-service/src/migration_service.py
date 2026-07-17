#!/usr/bin/env python3
"""
Knowledge Service Database Migration Service
ADR-0015 Phase 2: Database-per-Service

Migrates knowledge registry from shared location to knowledge-service ownership.
Following the pattern established by truth-service migration infrastructure.

Migration Strategy:
1. Extract shared registry.sqlite to knowledge-service ownership
2. Add audit trails and enhanced schema capabilities
3. Validate data integrity and namespace uniqueness
4. Provide dual-access mode for compatibility during migration
5. Enable backup and recovery capabilities
"""

import asyncio
import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

class KnowledgeMigrationService:
    """Handles migration of knowledge registry from shared to service-owned database."""

    def __init__(self, target_database_path: str, source_database_path: str = None):
        self.target_database_path = target_database_path
        self.source_database_path = source_database_path or self._find_source_database()
        self.migration_stats = {}

    def _find_source_database(self) -> str:
        """Find the shared registry.sqlite database."""
        project_root = Path(__file__).parents[3]  # Back to babyAI root
        shared_registry = project_root / "shared" / "babyai_shared" / "knowledge" / "registry.sqlite"
        return str(shared_registry)

    async def migration_needed(self) -> bool:
        """Check if migration is needed."""
        try:
            # Check if source database exists
            source_exists = Path(self.source_database_path).exists()
            if not source_exists:
                logger.info("No shared registry.sqlite found - migration not needed")
                return False

            # Check if target database exists and has data
            target_exists = Path(self.target_database_path).exists()
            if target_exists:
                async with aiosqlite.connect(self.target_database_path) as conn:
                    cursor = await conn.execute("SELECT COUNT(*) FROM librarians")
                    row_count = (await cursor.fetchone())[0]
                    if row_count > 0:
                        logger.info(f"Target database already has {row_count} librarians")
                        return False

            logger.info("Shared registry.sqlite exists and target is empty - migration needed")
            return True

        except Exception as e:
            logger.error(f"Error checking migration status: {e}")
            return False

    async def get_migration_status(self) -> Dict[str, Any]:
        """Get current migration status and progress."""
        status = {
            "migration_needed": await self.migration_needed(),
            "source_path": self.source_database_path,
            "source_exists": Path(self.source_database_path).exists(),
            "source_size_bytes": 0,
            "target_path": self.target_database_path,
            "target_exists": Path(self.target_database_path).exists(),
            "target_rows": 0,
            "migration_log": [],
            "last_checked": datetime.now(timezone.utc).isoformat()
        }

        try:
            # Get source database stats
            if status["source_exists"]:
                status["source_size_bytes"] = Path(self.source_database_path).stat().st_size

                async with aiosqlite.connect(self.source_database_path) as source_conn:
                    cursor = await source_conn.execute("SELECT COUNT(*) FROM librarians")
                    status["source_librarians"] = (await cursor.fetchone())[0]

                    # Get sample of librarian namespaces
                    cursor = await source_conn.execute(
                        "SELECT namespace, created_at, last_ingest FROM librarians ORDER BY last_ingest DESC LIMIT 5"
                    )
                    recent_librarians = await cursor.fetchall()
                    status["recent_librarians"] = [
                        {"namespace": row[0], "created_at": row[1], "last_ingest": row[2]}
                        for row in recent_librarians
                    ]

            # Get target database stats
            if status["target_exists"]:
                async with aiosqlite.connect(self.target_database_path) as target_conn:
                    cursor = await target_conn.execute("SELECT COUNT(*) FROM librarians")
                    status["target_rows"] = (await cursor.fetchone())[0]

                    # Check if enhanced schema exists
                    cursor = await target_conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                    tables = [row[0] for row in await cursor.fetchall()]
                    status["target_tables"] = tables

                    # Get migration log if it exists
                    if "knowledge_migration_log" in tables:
                        cursor = await target_conn.execute("""
                            SELECT operation, records_processed, status, started_at, completed_at, error_message
                            FROM knowledge_migration_log
                            ORDER BY started_at DESC
                            LIMIT 10
                        """)
                        status["migration_log"] = [dict(zip([col[0] for col in cursor.description], row)) for row in await cursor.fetchall()]

        except Exception as e:
            logger.error(f"Error getting migration status: {e}")
            status["error"] = str(e)

        return status

    async def migrate_registry_data(self) -> Dict[str, Any]:
        """Perform complete migration of knowledge registry."""
        start_time = datetime.now(timezone.utc)
        results = {
            "status": "started",
            "started_at": start_time.isoformat(),
            "librarians_migrated": 0,
            "validation_results": {},
            "errors": []
        }

        try:
            logger.info("Starting knowledge registry migration...")

            # Check if migration is needed
            if not await self.migration_needed():
                results["status"] = "skipped"
                results["message"] = "Migration not needed"
                return results

            # Create migration log entry
            migration_id = await self._create_migration_log("registry_migration")

            # Initialize target database with enhanced schema
            await self._initialize_target_database()

            # Migrate librarian data
            migration_result = await self._migrate_librarians()
            results.update(migration_result)

            # Validate migrated data
            validation_result = await self._validate_migration()
            results["validation_results"] = validation_result

            # Update final status
            if not results["errors"] and validation_result.get("valid", False):
                results["status"] = "completed"
                await self._complete_migration_log(migration_id, "success")
            else:
                results["status"] = "completed_with_errors"
                await self._complete_migration_log(migration_id, "partial")

        except Exception as e:
            error_msg = f"Migration failed: {e}"
            logger.error(error_msg)
            results["status"] = "failed"
            results["error"] = error_msg
            results["errors"].append(error_msg)

        finally:
            results["completed_at"] = datetime.now(timezone.utc).isoformat()
            results["duration_seconds"] = (
                datetime.now(timezone.utc) - start_time
            ).total_seconds()

        logger.info(f"Migration completed with status: {results['status']}")
        return results

    async def _initialize_target_database(self):
        """Initialize target database with enhanced schema."""
        logger.info("Initializing target database schema...")

        # Ensure target directory exists
        Path(self.target_database_path).parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(self.target_database_path) as conn:
            # Create enhanced librarians table (compatible with original)
            await conn.executescript("""
                CREATE TABLE IF NOT EXISTS librarians (
                    namespace TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL,
                    snapshot_ref TEXT NOT NULL,
                    root_paths_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_ingest TEXT NOT NULL,
                    -- Enhanced fields for service ownership
                    description TEXT,
                    tags_json TEXT DEFAULT '[]',
                    priority TEXT DEFAULT 'normal',
                    auto_ingest INTEGER DEFAULT 1,
                    validation_rules_json TEXT DEFAULT '{}',
                    created_by TEXT,
                    updated_by TEXT,
                    updated_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_librarians_snapshot_id ON librarians (snapshot_id);
                CREATE INDEX IF NOT EXISTS idx_librarians_created_at ON librarians (created_at);
                CREATE INDEX IF NOT EXISTS idx_librarians_last_ingest ON librarians (last_ingest);
                CREATE INDEX IF NOT EXISTS idx_librarians_priority ON librarians (priority);

                -- Enhanced tables for service capabilities
                CREATE TABLE IF NOT EXISTS namespace_reservations (
                    namespace TEXT PRIMARY KEY,
                    reserved_by TEXT NOT NULL,
                    reserved_at TEXT NOT NULL,
                    expires_at TEXT,
                    reservation_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS knowledge_audit_log (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    namespace TEXT,
                    operation_data_json TEXT,
                    performed_by TEXT,
                    performed_at TEXT NOT NULL,
                    correlation_id TEXT,
                    source_service TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_audit_log_performed_at ON knowledge_audit_log (performed_at);
                CREATE INDEX IF NOT EXISTS idx_audit_log_namespace ON knowledge_audit_log (namespace);
                CREATE INDEX IF NOT EXISTS idx_audit_log_correlation_id ON knowledge_audit_log (correlation_id);

                CREATE TABLE IF NOT EXISTS knowledge_migration_log (
                    migration_id TEXT,
                    operation TEXT NOT NULL,
                    records_processed INTEGER DEFAULT 0,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    error_message TEXT,
                    migration_data_json TEXT
                );
            """)

    async def _migrate_librarians(self) -> Dict[str, Any]:
        """Migrate librarian records from source to target database."""
        result = {
            "librarians_migrated": 0,
            "librarians_skipped": 0,
            "errors": [],
            "namespace_conflicts": []
        }

        try:
            logger.info("Migrating librarian records...")

            # Read all librarians from source
            async with aiosqlite.connect(self.source_database_path) as source_conn:
                cursor = await source_conn.execute("""
                    SELECT namespace, snapshot_id, snapshot_ref, root_paths_json, created_at, last_ingest
                    FROM librarians
                    ORDER BY created_at ASC
                """)
                source_librarians = await cursor.fetchall()

            if not source_librarians:
                logger.info("No librarians found in source database")
                return result

            # Migrate each librarian to target database
            async with aiosqlite.connect(self.target_database_path) as target_conn:
                for librarian in source_librarians:
                    try:
                        namespace, snapshot_id, snapshot_ref, root_paths_json, created_at, last_ingest = librarian

                        # Validate data before insertion
                        if not self._validate_librarian_data(namespace, snapshot_id, snapshot_ref, root_paths_json):
                            result["librarians_skipped"] += 1
                            result["errors"].append(f"Invalid data for namespace: {namespace}")
                            continue

                        # Check for namespace conflicts
                        conflict_cursor = await target_conn.execute(
                            "SELECT namespace FROM librarians WHERE namespace = ?", (namespace,)
                        )
                        if await conflict_cursor.fetchone():
                            result["namespace_conflicts"].append(namespace)
                            logger.warning(f"Namespace conflict detected: {namespace}")
                            continue

                        # Insert librarian with enhanced fields
                        await target_conn.execute("""
                            INSERT INTO librarians (
                                namespace, snapshot_id, snapshot_ref, root_paths_json,
                                created_at, last_ingest, updated_at, created_by
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            namespace, snapshot_id, snapshot_ref, root_paths_json,
                            created_at, last_ingest, datetime.now(timezone.utc).isoformat(),
                            "migration_service"
                        ))

                        result["librarians_migrated"] += 1

                        # Log the migration operation
                        await self._log_audit_operation("librarian_migrated", namespace, {
                            "source": "shared_registry",
                            "target": "knowledge_service",
                            "snapshot_id": snapshot_id
                        })

                    except Exception as e:
                        error_msg = f"Error migrating librarian {namespace}: {e}"
                        logger.error(error_msg)
                        result["errors"].append(error_msg)
                        result["librarians_skipped"] += 1

                # Commit all changes
                await target_conn.commit()

        except Exception as e:
            error_msg = f"Error during librarian migration: {e}"
            logger.error(error_msg)
            result["errors"].append(error_msg)

        logger.info(f"Migration complete: {result['librarians_migrated']} migrated, {result['librarians_skipped']} skipped")
        return result

    def _validate_librarian_data(self, namespace: str, snapshot_id: str, snapshot_ref: str, root_paths_json: str) -> bool:
        """Validate librarian data before migration."""
        try:
            # Check namespace format
            if not namespace or len(namespace) > 100:
                return False

            # Check required fields
            if not snapshot_id or not snapshot_ref:
                return False

            # Validate JSON paths
            root_paths = json.loads(root_paths_json or "[]")
            if not isinstance(root_paths, list):
                return False

            # Check path limits
            if len(root_paths) > 50:
                logger.warning(f"Namespace {namespace} has {len(root_paths)} paths (limit: 50)")
                return False

            return True

        except Exception as e:
            logger.error(f"Validation error for namespace {namespace}: {e}")
            return False

    async def _validate_migration(self) -> Dict[str, Any]:
        """Validate the migrated data integrity."""
        validation = {
            "valid": True,
            "checks_performed": [],
            "errors": [],
            "warnings": []
        }

        try:
            # Compare record counts
            async with aiosqlite.connect(self.source_database_path) as source_conn:
                cursor = await source_conn.execute("SELECT COUNT(*) FROM librarians")
                source_count = (await cursor.fetchone())[0]

            async with aiosqlite.connect(self.target_database_path) as target_conn:
                cursor = await target_conn.execute("SELECT COUNT(*) FROM librarians")
                target_count = (await cursor.fetchone())[0]

            count_check = {
                "check": "record_count",
                "source_count": source_count,
                "target_count": target_count,
                "match": source_count == target_count
            }
            validation["checks_performed"].append(count_check)

            if not count_check["match"]:
                validation["errors"].append(f"Record count mismatch: source={source_count}, target={target_count}")
                validation["valid"] = False

            # Validate sample records for content integrity
            await self._validate_sample_records(validation)

            # Check namespace uniqueness
            await self._validate_namespace_uniqueness(validation)

        except Exception as e:
            validation["valid"] = False
            validation["errors"].append(f"Validation failed: {e}")

        return validation

    async def _validate_sample_records(self, validation: Dict[str, Any]):
        """Validate a sample of migrated records for content integrity."""
        try:
            # Get sample from source
            async with aiosqlite.connect(self.source_database_path) as source_conn:
                cursor = await source_conn.execute("""
                    SELECT namespace, snapshot_id, root_paths_json
                    FROM librarians
                    ORDER BY created_at DESC
                    LIMIT 5
                """)
                source_samples = await cursor.fetchall()

            # Compare with target
            async with aiosqlite.connect(self.target_database_path) as target_conn:
                for sample in source_samples:
                    namespace, source_snapshot_id, source_paths = sample

                    cursor = await target_conn.execute(
                        "SELECT snapshot_id, root_paths_json FROM librarians WHERE namespace = ?",
                        (namespace,)
                    )
                    target_record = await cursor.fetchone()

                    if not target_record:
                        validation["errors"].append(f"Missing record in target: {namespace}")
                        validation["valid"] = False
                        continue

                    target_snapshot_id, target_paths = target_record

                    # Compare critical data
                    if source_snapshot_id != target_snapshot_id:
                        validation["errors"].append(f"Snapshot ID mismatch for {namespace}")
                        validation["valid"] = False

                    if source_paths != target_paths:
                        validation["errors"].append(f"Root paths mismatch for {namespace}")
                        validation["valid"] = False

        except Exception as e:
            validation["errors"].append(f"Sample validation failed: {e}")
            validation["valid"] = False

    async def _validate_namespace_uniqueness(self, validation: Dict[str, Any]):
        """Validate namespace uniqueness in target database."""
        try:
            async with aiosqlite.connect(self.target_database_path) as target_conn:
                cursor = await target_conn.execute("""
                    SELECT namespace, COUNT(*) as count
                    FROM librarians
                    GROUP BY namespace
                    HAVING COUNT(*) > 1
                """)
                duplicates = await cursor.fetchall()

                if duplicates:
                    validation["valid"] = False
                    for namespace, count in duplicates:
                        validation["errors"].append(f"Duplicate namespace: {namespace} (count: {count})")

        except Exception as e:
            validation["errors"].append(f"Uniqueness validation failed: {e}")
            validation["valid"] = False

    async def _create_migration_log(self, operation: str) -> str:
        """Create migration log entry and return log ID."""
        log_id = f"migration_{int(datetime.now(timezone.utc).timestamp())}"

        try:
            async with aiosqlite.connect(self.target_database_path) as conn:
                await conn.execute("""
                    INSERT INTO knowledge_migration_log
                    (migration_id, operation, status, started_at)
                    VALUES (?, ?, 'started', ?)
                """, (log_id, operation, datetime.now(timezone.utc).isoformat()))
                await conn.commit()

        except Exception as e:
            logger.error(f"Error creating migration log: {e}")

        return log_id

    async def _complete_migration_log(self, migration_id: str, status: str):
        """Complete migration log entry."""
        try:
            async with aiosqlite.connect(self.target_database_path) as conn:
                await conn.execute("""
                    UPDATE knowledge_migration_log
                    SET status = ?, completed_at = ?
                    WHERE migration_id = ? AND operation = 'registry_migration'
                """, (status, datetime.now(timezone.utc).isoformat(), migration_id))
                await conn.commit()

        except Exception as e:
            logger.error(f"Error completing migration log: {e}")

    async def _log_audit_operation(self, operation: str, namespace: str, data: Dict[str, Any]):
        """Log operation to audit trail."""
        try:
            async with aiosqlite.connect(self.target_database_path) as conn:
                await conn.execute("""
                    INSERT INTO knowledge_audit_log
                    (operation, namespace, operation_data_json, performed_by, performed_at, source_service)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    operation, namespace, json.dumps(data),
                    "migration_service", datetime.now(timezone.utc).isoformat(),
                    "knowledge-service"
                ))

        except Exception as e:
            logger.error(f"Error logging audit operation: {e}")

    async def cleanup_shared_registry(self) -> Dict[str, Any]:
        """Remove shared registry access after successful migration."""
        result = {
            "status": "started",
            "actions_taken": [],
            "errors": []
        }

        try:
            # Validate migration is complete first
            status = await self.get_migration_status()
            if status.get("migration_needed", True):
                result["status"] = "skipped"
                result["error"] = "Migration not complete - cannot cleanup shared registry"
                return result

            # Create backup of shared registry
            shared_registry_path = Path(self.source_database_path)
            if shared_registry_path.exists():
                backup_path = shared_registry_path.with_suffix('.sqlite.migrated_backup')
                shared_registry_path.rename(backup_path)
                result["actions_taken"].append(f"Moved shared registry to backup: {backup_path}")

            result["status"] = "completed"
            logger.info("Shared registry cleanup completed successfully")

        except Exception as e:
            error_msg = f"Error cleaning up shared registry: {e}"
            logger.error(error_msg)
            result["status"] = "failed"
            result["error"] = error_msg
            result["errors"].append(error_msg)

        return result

# Async context manager for database connections
@asynccontextmanager
async def get_knowledge_migration_service(target_database_path: str, source_database_path: str = None):
    """Context manager for knowledge migration service."""
    service = KnowledgeMigrationService(target_database_path, source_database_path)
    try:
        yield service
    finally:
        # Cleanup if needed
        pass

if __name__ == "__main__":
    import sys
    import os

    # Example usage
    async def main():
        target_db = os.getenv("TARGET_DATABASE", "knowledge_registry.db")

        async with get_knowledge_migration_service(target_db) as migration_service:
            # Check migration status
            status = await migration_service.get_migration_status()
            print(f"Migration status: {json.dumps(status, indent=2)}")

            # Run migration if needed
            if status.get("migration_needed"):
                print("Starting migration...")
                results = await migration_service.migrate_registry_data()
                print(f"Migration results: {json.dumps(results, indent=2)}")
            else:
                print("Migration not needed")

    asyncio.run(main())