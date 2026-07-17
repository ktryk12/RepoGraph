"""
PostgreSQL store for knowledge service

Handles knowledge registry, migration management, audit logging, and namespace reservations.
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


class PostgreSQLKnowledgeStore:
    """PostgreSQL storage for knowledge registry management"""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool: Optional[Pool] = None

    async def initialize(self):
        """Initialize the connection pool"""
        try:
            self.pool = await asyncpg.create_pool(
                self.database_url,
                min_size=2,
                max_size=12,
                command_timeout=60
            )
            logger.info("PostgreSQL knowledge store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize knowledge store: {e}")
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

    # === LIBRARIAN REGISTRY ===

    async def create_librarian(
        self,
        namespace: str,
        snapshot_id: str,
        snapshot_ref: str,
        root_paths: List[str],
        description: Optional[str] = None,
        tags: List[str] = None,
        priority: str = "normal",
        auto_ingest: bool = True,
        validation_rules: Dict[str, Any] = None,
        created_by: str = "knowledge-service"
    ) -> None:
        """Create a new librarian record"""
        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO librarians (
                    namespace, snapshot_id, snapshot_ref, root_paths_json,
                    created_at, last_ingest, description, tags_json, priority,
                    auto_ingest, validation_rules_json, created_by, updated_by, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
            """,
                namespace, snapshot_id, snapshot_ref, json.dumps(root_paths or []),
                now, now, description, json.dumps(tags or []), priority,
                auto_ingest, json.dumps(validation_rules or {}),
                created_by, created_by, now
            )

    async def update_librarian(
        self,
        namespace: str,
        snapshot_id: Optional[str] = None,
        snapshot_ref: Optional[str] = None,
        root_paths: Optional[List[str]] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        priority: Optional[str] = None,
        auto_ingest: Optional[bool] = None,
        validation_rules: Optional[Dict[str, Any]] = None,
        updated_by: str = "knowledge-service"
    ) -> bool:
        """Update an existing librarian record"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        # Build dynamic update query
        update_fields = []
        params = []
        param_count = 0

        if snapshot_id is not None:
            param_count += 1
            update_fields.append(f"snapshot_id = ${param_count}")
            params.append(snapshot_id)

        if snapshot_ref is not None:
            param_count += 1
            update_fields.append(f"snapshot_ref = ${param_count}")
            params.append(snapshot_ref)

        if root_paths is not None:
            param_count += 1
            update_fields.append(f"root_paths_json = ${param_count}")
            params.append(json.dumps(root_paths))

        if description is not None:
            param_count += 1
            update_fields.append(f"description = ${param_count}")
            params.append(description)

        if tags is not None:
            param_count += 1
            update_fields.append(f"tags_json = ${param_count}")
            params.append(json.dumps(tags))

        if priority is not None:
            param_count += 1
            update_fields.append(f"priority = ${param_count}")
            params.append(priority)

        if auto_ingest is not None:
            param_count += 1
            update_fields.append(f"auto_ingest = ${param_count}")
            params.append(auto_ingest)

        if validation_rules is not None:
            param_count += 1
            update_fields.append(f"validation_rules_json = ${param_count}")
            params.append(json.dumps(validation_rules))

        if not update_fields:
            return False  # No updates specified

        # Always update these fields
        param_count += 1
        update_fields.append(f"updated_by = ${param_count}")
        params.append(updated_by)

        param_count += 1
        update_fields.append(f"updated_at = ${param_count}")
        params.append(datetime.now(timezone.utc).isoformat())

        param_count += 1
        params.append(namespace)  # for WHERE clause

        query = f"""
            UPDATE librarians
            SET {', '.join(update_fields)}
            WHERE namespace = ${param_count}
        """

        async with self.pool.acquire() as conn:
            result = await conn.execute(query, *params)
            return "UPDATE 1" in str(result)

    async def get_librarian(self, namespace: str) -> Optional[Dict[str, Any]]:
        """Get a librarian by namespace"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM librarians WHERE namespace = $1", namespace
            )

            if not row:
                return None

            result = dict(row)
            # Parse JSON fields back to objects
            for json_field, default_val in [
                ("root_paths_json", []), ("tags_json", []), ("validation_rules_json", {})
            ]:
                if result.get(json_field):
                    try:
                        field_name = json_field.replace("_json", "")
                        result[field_name] = json.loads(result[json_field])
                        del result[json_field]  # Remove raw JSON field
                    except json.JSONDecodeError:
                        result[json_field.replace("_json", "")] = default_val

            return result

    async def list_librarians(
        self,
        priority: Optional[str] = None,
        auto_ingest: Optional[bool] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """List librarians with optional filtering"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        query_parts = ["SELECT * FROM librarians WHERE 1=1"]
        params = []
        param_count = 0

        if priority:
            param_count += 1
            query_parts.append(f"AND priority = ${param_count}")
            params.append(priority)

        if auto_ingest is not None:
            param_count += 1
            query_parts.append(f"AND auto_ingest = ${param_count}")
            params.append(auto_ingest)

        query_parts.append("ORDER BY last_ingest DESC")
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
                for json_field, default_val in [
                    ("root_paths_json", []), ("tags_json", []), ("validation_rules_json", {})
                ]:
                    if result.get(json_field):
                        try:
                            field_name = json_field.replace("_json", "")
                            result[field_name] = json.loads(result[json_field])
                            del result[json_field]
                        except json.JSONDecodeError:
                            result[json_field.replace("_json", "")] = default_val
                results.append(result)

            return results

    async def delete_librarian(self, namespace: str) -> bool:
        """Delete a librarian record"""
        async with self.transaction() as conn:
            result = await conn.execute(
                "DELETE FROM librarians WHERE namespace = $1", namespace
            )
            return "DELETE 1" in str(result)

    async def update_last_ingest(self, namespace: str) -> bool:
        """Update the last ingest timestamp"""
        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            result = await conn.execute(
                "UPDATE librarians SET last_ingest = $1 WHERE namespace = $2",
                now, namespace
            )
            return "UPDATE 1" in str(result)

    # === NAMESPACE RESERVATIONS ===

    async def reserve_namespace(
        self,
        namespace: str,
        reserved_by: str,
        reservation_reason: str,
        expires_at: Optional[datetime] = None
    ) -> bool:
        """Reserve a namespace to prevent conflicts"""
        now = datetime.now(timezone.utc).isoformat()
        expires_iso = expires_at.isoformat() if expires_at else None

        try:
            async with self.transaction() as conn:
                await conn.execute("""
                    INSERT INTO namespace_reservations
                    (namespace, reserved_by, reserved_at, expires_at, reservation_reason)
                    VALUES ($1, $2, $3, $4, $5)
                """, namespace, reserved_by, now, expires_iso, reservation_reason)
                return True

        except asyncpg.UniqueViolationError:
            return False  # Namespace already reserved

    async def release_namespace_reservation(self, namespace: str, released_by: str) -> bool:
        """Release a namespace reservation"""
        async with self.transaction() as conn:
            result = await conn.execute(
                "DELETE FROM namespace_reservations WHERE namespace = $1", namespace
            )
            return "DELETE 1" in str(result)

    async def get_namespace_reservation(self, namespace: str) -> Optional[Dict[str, Any]]:
        """Get namespace reservation details"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM namespace_reservations WHERE namespace = $1", namespace
            )
            return dict(row) if row else None

    async def cleanup_expired_reservations(self) -> int:
        """Clean up expired namespace reservations"""
        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            result = await conn.execute(
                "DELETE FROM namespace_reservations WHERE expires_at IS NOT NULL AND expires_at < $1",
                now
            )
            deleted_count = int(result.split()[-1]) if result.split()[-1].isdigit() else 0
            return deleted_count

    # === AUDIT LOGGING ===

    async def log_audit_operation(
        self,
        operation: str,
        namespace: Optional[str] = None,
        operation_data: Optional[Dict[str, Any]] = None,
        performed_by: str = "knowledge-service",
        correlation_id: Optional[str] = None,
        source_service: str = "knowledge-service"
    ) -> None:
        """Log an audit operation"""
        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO knowledge_audit_log (
                    operation, namespace, operation_data_json, performed_by,
                    performed_at, correlation_id, source_service
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
                operation, namespace, json.dumps(operation_data or {}),
                performed_by, datetime.now(timezone.utc).isoformat(),
                correlation_id, source_service
            )

    async def get_audit_log(
        self,
        namespace: Optional[str] = None,
        operation: Optional[str] = None,
        hours: int = 24,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Get audit log entries with optional filtering"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        query_parts = ["SELECT * FROM knowledge_audit_log WHERE performed_at >= $1"]
        params = [cutoff_time]
        param_count = 1

        if namespace:
            param_count += 1
            query_parts.append(f"AND namespace = ${param_count}")
            params.append(namespace)

        if operation:
            param_count += 1
            query_parts.append(f"AND operation = ${param_count}")
            params.append(operation)

        query_parts.append("ORDER BY performed_at DESC")
        param_count += 1
        query_parts.append(f"LIMIT ${param_count}")
        params.append(limit)

        query = " ".join(query_parts)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

            results = []
            for row in rows:
                result = dict(row)
                # Parse operation data JSON
                if result.get("operation_data_json"):
                    try:
                        result["operation_data"] = json.loads(result["operation_data_json"])
                        del result["operation_data_json"]
                    except json.JSONDecodeError:
                        result["operation_data"] = {}
                results.append(result)

            return results

    # === MIGRATION MANAGEMENT ===

    async def create_migration_log(self, operation: str, migration_data: Optional[Dict[str, Any]] = None) -> str:
        """Create a migration log entry"""
        migration_id = f"migration_{int(datetime.now(timezone.utc).timestamp())}"

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO knowledge_migration_log (
                    migration_id, operation, records_processed, status,
                    started_at, migration_data_json
                ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
                migration_id, operation, 0, "started",
                datetime.now(timezone.utc).isoformat(),
                json.dumps(migration_data or {})
            )

        return migration_id

    async def update_migration_log(
        self,
        migration_id: str,
        status: str,
        records_processed: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> None:
        """Update migration log status"""
        now = datetime.now(timezone.utc).isoformat()

        async with self.transaction() as conn:
            if status in ["completed", "failed"]:
                # Final update with completion time
                await conn.execute("""
                    UPDATE knowledge_migration_log
                    SET status = $2, completed_at = $3, records_processed = COALESCE($4, records_processed),
                        error_message = $5
                    WHERE migration_id = $1
                """, migration_id, status, now, records_processed, error_message)
            else:
                # Progress update
                await conn.execute("""
                    UPDATE knowledge_migration_log
                    SET status = $2, records_processed = COALESCE($3, records_processed),
                        error_message = $4
                    WHERE migration_id = $1
                """, migration_id, status, records_processed, error_message)

    async def get_migration_status(self, migration_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get migration status"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        if migration_id:
            query = "SELECT * FROM knowledge_migration_log WHERE migration_id = $1"
            params = [migration_id]
        else:
            query = "SELECT * FROM knowledge_migration_log ORDER BY started_at DESC LIMIT 20"
            params = []

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)

            results = []
            for row in rows:
                result = dict(row)
                # Parse migration data JSON
                if result.get("migration_data_json"):
                    try:
                        result["migration_data"] = json.loads(result["migration_data_json"])
                        del result["migration_data_json"]
                    except json.JSONDecodeError:
                        result["migration_data"] = {}
                results.append(result)

            return results

    # === ANALYTICS ===

    async def get_knowledge_statistics(self) -> Dict[str, Any]:
        """Get knowledge registry statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            # Total librarians
            total_librarians = await conn.fetchval("SELECT COUNT(*) FROM librarians")

            # Librarians by priority
            priority_stats = await conn.fetch("""
                SELECT priority, COUNT(*) as count
                FROM librarians
                GROUP BY priority
                ORDER BY count DESC
            """)

            # Auto-ingest statistics
            auto_ingest_stats = await conn.fetchrow("""
                SELECT
                    COUNT(*) FILTER (WHERE auto_ingest = TRUE) as auto_ingest_enabled,
                    COUNT(*) FILTER (WHERE auto_ingest = FALSE) as auto_ingest_disabled
                FROM librarians
            """)

            # Recent activity (last 7 days)
            week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
            recent_operations = await conn.fetch("""
                SELECT operation, COUNT(*) as count
                FROM knowledge_audit_log
                WHERE performed_at >= $1
                GROUP BY operation
                ORDER BY count DESC
                LIMIT 10
            """, week_ago)

            # Active namespace reservations
            active_reservations = await conn.fetchval(
                "SELECT COUNT(*) FROM namespace_reservations"
            )

            # Migration status
            recent_migrations = await conn.fetch("""
                SELECT operation, status, COUNT(*) as count
                FROM knowledge_migration_log
                GROUP BY operation, status
                ORDER BY operation
            """)

            return {
                "total_librarians": total_librarians,
                "librarians_by_priority": {row["priority"]: row["count"] for row in priority_stats},
                "auto_ingest": dict(auto_ingest_stats) if auto_ingest_stats else {},
                "recent_operations": [{"operation": row["operation"], "count": row["count"]} for row in recent_operations],
                "active_reservations": active_reservations,
                "migrations": [{"operation": row["operation"], "status": row["status"], "count": row["count"]} for row in recent_migrations],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

    async def cleanup_old_audit_logs(self, days: int = 90) -> int:
        """Clean up old audit log entries"""
        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.transaction() as conn:
            result = await conn.execute(
                "DELETE FROM knowledge_audit_log WHERE performed_at < $1", cutoff_time
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
                librarians_count = await conn.fetchval("SELECT COUNT(*) FROM librarians")
                audit_log_count = await conn.fetchval("SELECT COUNT(*) FROM knowledge_audit_log")
                reservations_count = await conn.fetchval("SELECT COUNT(*) FROM namespace_reservations")
                migrations_count = await conn.fetchval("SELECT COUNT(*) FROM knowledge_migration_log")

                return {
                    "status": "healthy" if result == 1 else "unhealthy",
                    "pool_size": self.pool.get_size(),
                    "librarians_registered": librarians_count,
                    "audit_log_entries": audit_log_count,
                    "active_reservations": reservations_count,
                    "migration_records": migrations_count,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }


# Global store instance
knowledge_store: Optional[PostgreSQLKnowledgeStore] = None


async def get_knowledge_store() -> PostgreSQLKnowledgeStore:
    """Get the global knowledge store instance"""
    global knowledge_store
    if knowledge_store is None:
        raise RuntimeError("Knowledge store not initialized")
    return knowledge_store


async def initialize_knowledge_store(database_url: str):
    """Initialize the global knowledge store"""
    global knowledge_store
    knowledge_store = PostgreSQLKnowledgeStore(database_url)
    await knowledge_store.initialize()


async def close_knowledge_store():
    """Close the global knowledge store"""
    global knowledge_store
    if knowledge_store:
        await knowledge_store.close()
        knowledge_store = None