#!/usr/bin/env python3
"""
Context Plane Database Migration Service
ADR-0015 Phase 2: Database-per-Service

Migrates context database from data-platform SQLite to context-plane PostgreSQL
Following the pattern established by truth-service migration infrastructure.

Migration Strategy:
1. Export data from SQLite (services/data-platform/src/storage/context_plane/index.db)
2. Transform and validate data for PostgreSQL schema
3. Import into context-plane PostgreSQL database
4. Validate data integrity and performance
5. Enable dual-read mode for compatibility
6. Cut over to PostgreSQL-only mode
"""

import asyncio
import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncpg
import aiosqlite
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

class ContextMigrationService:
    """Handles migration of context database from SQLite to PostgreSQL."""

    def __init__(self, postgresql_url: str, sqlite_path: str = None):
        self.postgresql_url = postgresql_url
        self.sqlite_path = sqlite_path or self._find_sqlite_path()
        self.migration_stats = {}

    def _find_sqlite_path(self) -> str:
        """Find the SQLite database path from data-platform."""
        project_root = Path(__file__).parents[3]  # Back to babyAI root
        sqlite_path = project_root / "services" / "data-platform" / "src" / "storage" / "context_plane" / "index.db"
        return str(sqlite_path)

    async def migration_needed(self) -> bool:
        """Check if migration is needed."""
        try:
            # Check if SQLite database exists
            sqlite_exists = Path(self.sqlite_path).exists()
            if not sqlite_exists:
                logger.info("No SQLite database found - migration not needed")
                return False

            # Check if PostgreSQL has data
            pg_conn = await asyncpg.connect(self.postgresql_url)
            try:
                # Check if context_entries table has data
                row_count = await pg_conn.fetchval("SELECT COUNT(*) FROM context_entries")
                if row_count > 0:
                    logger.info(f"PostgreSQL already has {row_count} context entries")
                    return False

                logger.info("SQLite database exists and PostgreSQL is empty - migration needed")
                return True
            finally:
                await pg_conn.close()

        except Exception as e:
            logger.error(f"Error checking migration status: {e}")
            return False

    async def get_migration_status(self) -> Dict[str, Any]:
        """Get current migration status and progress."""
        status = {
            "migration_needed": await self.migration_needed(),
            "sqlite_path": self.sqlite_path,
            "sqlite_exists": Path(self.sqlite_path).exists(),
            "sqlite_size_bytes": 0,
            "postgresql_url": self.postgresql_url.split('@')[0] + '@***',  # Hide credentials
            "postgresql_rows": 0,
            "migration_log": [],
            "last_checked": datetime.now(timezone.utc).isoformat()
        }

        try:
            # Get SQLite stats
            if status["sqlite_exists"]:
                status["sqlite_size_bytes"] = Path(self.sqlite_path).stat().st_size

                async with aiosqlite.connect(self.sqlite_path) as sqlite_conn:
                    # Get table counts
                    tables = ["context_entries", "context_payloads", "dep_graph", "context_cache"]
                    sqlite_stats = {}

                    for table in tables:
                        try:
                            cursor = await sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}")
                            count = (await cursor.fetchone())[0]
                            sqlite_stats[table] = count
                        except sqlite3.OperationalError:
                            sqlite_stats[table] = 0  # Table doesn't exist

                    status["sqlite_stats"] = sqlite_stats

            # Get PostgreSQL stats
            pg_conn = await asyncpg.connect(self.postgresql_url)
            try:
                # Check if tables exist and get counts
                pg_stats = {}
                tables = ["context_entries", "context_payloads", "dep_graph", "context_cache"]

                for table in tables:
                    try:
                        count = await pg_conn.fetchval(f"SELECT COUNT(*) FROM {table}")
                        pg_stats[table] = count
                        status["postgresql_rows"] += count
                    except asyncpg.UndefinedTableError:
                        pg_stats[table] = 0  # Table doesn't exist

                status["postgresql_stats"] = pg_stats
            finally:
                await pg_conn.close()

            # Get migration log if it exists
            try:
                async with asyncpg.connect(self.postgresql_url) as pg_conn:
                    migration_log = await pg_conn.fetch("""
                        SELECT operation, table_name, records_processed,
                               status, started_at, completed_at, error_message
                        FROM context_migration_log
                        ORDER BY started_at DESC
                        LIMIT 50
                    """)
                    status["migration_log"] = [dict(row) for row in migration_log]
            except:
                status["migration_log"] = []

        except Exception as e:
            logger.error(f"Error getting migration status: {e}")
            status["error"] = str(e)

        return status

    async def migrate_all_data(self) -> Dict[str, Any]:
        """Perform complete migration from SQLite to PostgreSQL."""
        start_time = datetime.now(timezone.utc)
        results = {
            "status": "started",
            "started_at": start_time.isoformat(),
            "tables_migrated": {},
            "total_records": 0,
            "errors": []
        }

        try:
            logger.info("Starting context database migration...")

            # Check if migration is needed
            if not await self.migration_needed():
                results["status"] = "skipped"
                results["message"] = "Migration not needed"
                return results

            # Create migration log entry
            migration_id = await self._create_migration_log("full_migration")

            # Migrate each table in order
            tables_to_migrate = [
                "context_entries",
                "context_payloads",
                "dep_graph",
                "context_cache"
            ]

            for table_name in tables_to_migrate:
                try:
                    logger.info(f"Migrating table: {table_name}")
                    table_result = await self._migrate_table(table_name)
                    results["tables_migrated"][table_name] = table_result
                    results["total_records"] += table_result.get("records_migrated", 0)

                    # Log table completion
                    await self._update_migration_log(migration_id, table_name, table_result)

                except Exception as e:
                    error_msg = f"Error migrating {table_name}: {e}"
                    logger.error(error_msg)
                    results["errors"].append(error_msg)

                    # Log error
                    await self._update_migration_log(migration_id, table_name, {
                        "status": "failed",
                        "error": str(e)
                    })

            # Validate migration
            validation_result = await self._validate_migration()
            results["validation"] = validation_result

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

    async def _migrate_table(self, table_name: str) -> Dict[str, Any]:
        """Migrate a specific table from SQLite to PostgreSQL."""
        result = {
            "table_name": table_name,
            "records_migrated": 0,
            "records_skipped": 0,
            "errors": [],
            "status": "started"
        }

        try:
            # Get table structure and data from SQLite
            async with aiosqlite.connect(self.sqlite_path) as sqlite_conn:
                # Check if table exists
                cursor = await sqlite_conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,)
                )
                if not await cursor.fetchone():
                    result["status"] = "skipped"
                    result["message"] = f"Table {table_name} not found in SQLite"
                    return result

                # Get all data from the table
                cursor = await sqlite_conn.execute(f"SELECT * FROM {table_name}")
                rows = await cursor.fetchall()

                if not rows:
                    result["status"] = "completed"
                    result["message"] = f"Table {table_name} is empty"
                    return result

                # Get column names
                cursor = await sqlite_conn.execute(f"PRAGMA table_info({table_name})")
                columns_info = await cursor.fetchall()
                column_names = [col[1] for col in columns_info]

            # Insert data into PostgreSQL
            async with asyncpg.connect(self.postgresql_url) as pg_conn:
                # Prepare insert statement
                placeholders = ', '.join(f'${i+1}' for i in range(len(column_names)))
                insert_sql = f"""
                    INSERT INTO {table_name} ({', '.join(column_names)})
                    VALUES ({placeholders})
                    ON CONFLICT DO NOTHING
                """

                # Insert in batches
                batch_size = 1000
                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i + batch_size]

                    # Transform data for PostgreSQL compatibility
                    transformed_batch = []
                    for row in batch:
                        transformed_row = await self._transform_row(table_name, column_names, row)
                        if transformed_row:
                            transformed_batch.append(transformed_row)

                    if transformed_batch:
                        try:
                            await pg_conn.executemany(insert_sql, transformed_batch)
                            result["records_migrated"] += len(transformed_batch)
                        except Exception as e:
                            error_msg = f"Error inserting batch starting at {i}: {e}"
                            logger.error(error_msg)
                            result["errors"].append(error_msg)
                            result["records_skipped"] += len(batch)

            result["status"] = "completed"

        except Exception as e:
            error_msg = f"Error migrating table {table_name}: {e}"
            logger.error(error_msg)
            result["status"] = "failed"
            result["error"] = str(e)
            result["errors"].append(error_msg)

        return result

    async def _transform_row(self, table_name: str, column_names: List[str], row: Tuple) -> Optional[List]:
        """Transform a SQLite row for PostgreSQL compatibility."""
        try:
            transformed = []

            for i, (col_name, value) in enumerate(zip(column_names, row)):
                # Handle JSON columns
                if col_name in ['metadata', 'payload', 'chunk_data', 'cache_data']:
                    if isinstance(value, str):
                        try:
                            # Validate JSON
                            json.loads(value)
                            transformed.append(value)
                        except:
                            # Invalid JSON, store as empty object
                            transformed.append('{}')
                    elif value is None:
                        transformed.append('{}')
                    else:
                        transformed.append(json.dumps(value))

                # Handle datetime columns
                elif col_name in ['created_at', 'updated_at', 'last_accessed', 'expires_at']:
                    if value:
                        # Convert to ISO format if needed
                        if isinstance(value, str):
                            transformed.append(value)
                        else:
                            transformed.append(datetime.fromtimestamp(value, timezone.utc).isoformat())
                    else:
                        transformed.append(None)

                # Handle text/varchar columns
                elif isinstance(value, str) and len(value) > 10000:
                    # Truncate very long strings
                    logger.warning(f"Truncating long value in {table_name}.{col_name}")
                    transformed.append(value[:10000])

                else:
                    transformed.append(value)

            return transformed

        except Exception as e:
            logger.error(f"Error transforming row in {table_name}: {e}")
            return None

    async def _validate_migration(self) -> Dict[str, Any]:
        """Validate the migrated data integrity."""
        validation = {
            "valid": True,
            "checks_performed": [],
            "errors": [],
            "warnings": []
        }

        try:
            # Check row counts match
            async with aiosqlite.connect(self.sqlite_path) as sqlite_conn:
                async with asyncpg.connect(self.postgresql_url) as pg_conn:

                    tables = ["context_entries", "context_payloads", "dep_graph", "context_cache"]

                    for table in tables:
                        try:
                            # Get SQLite count
                            cursor = await sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}")
                            sqlite_count = (await cursor.fetchone())[0]

                            # Get PostgreSQL count
                            pg_count = await pg_conn.fetchval(f"SELECT COUNT(*) FROM {table}")

                            check = {
                                "table": table,
                                "sqlite_count": sqlite_count,
                                "postgresql_count": pg_count,
                                "match": sqlite_count == pg_count
                            }

                            validation["checks_performed"].append(check)

                            if not check["match"]:
                                error_msg = f"Row count mismatch in {table}: SQLite={sqlite_count}, PostgreSQL={pg_count}"
                                validation["errors"].append(error_msg)
                                validation["valid"] = False

                        except Exception as e:
                            error_msg = f"Error validating {table}: {e}"
                            validation["errors"].append(error_msg)
                            validation["valid"] = False

            # Check data integrity with sample validation
            await self._validate_sample_data(validation)

        except Exception as e:
            validation["valid"] = False
            validation["errors"].append(f"Validation failed: {e}")

        return validation

    async def _validate_sample_data(self, validation: Dict[str, Any]):
        """Validate a sample of migrated data for content integrity."""
        try:
            async with aiosqlite.connect(self.sqlite_path) as sqlite_conn:
                async with asyncpg.connect(self.postgresql_url) as pg_conn:

                    # Sample context_entries for content validation
                    cursor = await sqlite_conn.execute("""
                        SELECT file_path, content_hash, chunk_data
                        FROM context_entries
                        ORDER BY RANDOM()
                        LIMIT 10
                    """)
                    sqlite_samples = await cursor.fetchall()

                    for sample in sqlite_samples:
                        file_path, content_hash, chunk_data = sample

                        # Get same record from PostgreSQL
                        pg_record = await pg_conn.fetchrow("""
                            SELECT file_path, content_hash, chunk_data::text
                            FROM context_entries
                            WHERE file_path = $1
                        """, file_path)

                        if not pg_record:
                            validation["errors"].append(f"Missing record in PostgreSQL: {file_path}")
                            validation["valid"] = False
                            continue

                        # Compare content hash
                        if content_hash != pg_record['content_hash']:
                            validation["errors"].append(f"Content hash mismatch for {file_path}")
                            validation["valid"] = False

                        # Compare chunk data
                        if chunk_data and pg_record['chunk_data']:
                            try:
                                sqlite_chunks = json.loads(chunk_data) if isinstance(chunk_data, str) else chunk_data
                                pg_chunks = json.loads(pg_record['chunk_data'])

                                if sqlite_chunks != pg_chunks:
                                    validation["warnings"].append(f"Chunk data difference in {file_path}")
                            except:
                                validation["warnings"].append(f"Could not compare chunk data for {file_path}")

        except Exception as e:
            validation["errors"].append(f"Sample validation failed: {e}")
            validation["valid"] = False

    async def _create_migration_log(self, operation: str) -> str:
        """Create migration log entry and return log ID."""
        log_id = f"migration_{int(datetime.now(timezone.utc).timestamp())}"

        try:
            async with asyncpg.connect(self.postgresql_url) as pg_conn:
                await pg_conn.execute("""
                    INSERT INTO context_migration_log
                    (migration_id, operation, status, started_at)
                    VALUES ($1, $2, 'started', $3)
                """, log_id, operation, datetime.now(timezone.utc))

        except Exception as e:
            logger.error(f"Error creating migration log: {e}")

        return log_id

    async def _update_migration_log(self, migration_id: str, table_name: str, result: Dict[str, Any]):
        """Update migration log with table progress."""
        try:
            async with asyncpg.connect(self.postgresql_url) as pg_conn:
                await pg_conn.execute("""
                    INSERT INTO context_migration_log
                    (migration_id, operation, table_name, records_processed, status, started_at, completed_at, error_message)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                    migration_id,
                    "table_migration",
                    table_name,
                    result.get("records_migrated", 0),
                    result.get("status", "unknown"),
                    datetime.now(timezone.utc),
                    datetime.now(timezone.utc),
                    result.get("error")
                )

        except Exception as e:
            logger.error(f"Error updating migration log: {e}")

    async def _complete_migration_log(self, migration_id: str, status: str):
        """Complete migration log entry."""
        try:
            async with asyncpg.connect(self.postgresql_url) as pg_conn:
                await pg_conn.execute("""
                    UPDATE context_migration_log
                    SET status = $2, completed_at = $3
                    WHERE migration_id = $1 AND operation = 'full_migration'
                """, migration_id, status, datetime.now(timezone.utc))

        except Exception as e:
            logger.error(f"Error completing migration log: {e}")

    async def cleanup_sqlite_access(self) -> Dict[str, Any]:
        """Remove SQLite database access from data-platform after successful migration."""
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
                result["error"] = "Migration not complete - cannot cleanup SQLite access"
                return result

            # Create backup of SQLite database
            sqlite_path = Path(self.sqlite_path)
            if sqlite_path.exists():
                backup_path = sqlite_path.with_suffix('.db.migrated_backup')
                sqlite_path.rename(backup_path)
                result["actions_taken"].append(f"Moved SQLite database to backup: {backup_path}")

            result["status"] = "completed"
            logger.info("SQLite cleanup completed successfully")

        except Exception as e:
            error_msg = f"Error cleaning up SQLite access: {e}"
            logger.error(error_msg)
            result["status"] = "failed"
            result["error"] = error_msg
            result["errors"].append(error_msg)

        return result

# Async context manager for database connections
@asynccontextmanager
async def get_migration_service(postgresql_url: str, sqlite_path: str = None):
    """Context manager for migration service."""
    service = ContextMigrationService(postgresql_url, sqlite_path)
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
        postgresql_url = os.getenv("DATABASE_URL", "postgresql://context_user:password@localhost/context_plane")

        async with get_migration_service(postgresql_url) as migration_service:
            # Check migration status
            status = await migration_service.get_migration_status()
            print(f"Migration status: {json.dumps(status, indent=2)}")

            # Run migration if needed
            if status.get("migration_needed"):
                print("Starting migration...")
                results = await migration_service.migrate_all_data()
                print(f"Migration results: {json.dumps(results, indent=2)}")
            else:
                print("Migration not needed")

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    asyncio.run(main())