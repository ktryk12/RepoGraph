"""
PostgreSQL store for provenance-service

Handles provenance graph edges, lineage tracking, graph analytics, validation results,
and audit trails for the service-owned provenance database.
"""

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any, Set, Tuple
from uuid import uuid4
import hashlib

import asyncpg
from asyncpg import Pool

logger = logging.getLogger(__name__)


class PostgreSQLProvenanceStore:
    """PostgreSQL storage for provenance graph data"""

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
            logger.info("PostgreSQL provenance store initialized")
        except Exception as e:
            logger.error(f"Failed to initialize provenance store: {e}")
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

    # === PROVENANCE EDGES ===

    async def add_provenance_edge(
        self,
        src_type: str,
        src_id: str,
        dst_type: str,
        dst_id: str,
        timestamp: Optional[str] = None,
        meta_data: Optional[Dict[str, Any]] = None,
        confidence: float = 1.0,
        created_by: Optional[str] = None
    ) -> str:
        """Add a provenance edge to the graph"""
        ts = timestamp or datetime.now(timezone.utc).isoformat()
        edge_hash = self._calculate_edge_hash(src_type, src_id, dst_type, dst_id, ts)

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO provenance_edges (
                    edge_hash, src_type, src_id, dst_type, dst_id, ts,
                    meta_json, confidence, created_by, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (edge_hash) DO UPDATE SET
                    meta_json = EXCLUDED.meta_json,
                    confidence = EXCLUDED.confidence,
                    updated_at = CURRENT_TIMESTAMP
            """,
                edge_hash, src_type, src_id, dst_type, dst_id, ts,
                json.dumps(meta_data or {}), confidence, created_by,
                datetime.now(timezone.utc).isoformat()
            )

            # Log audit operation
            await self._log_audit_operation(conn, "edge_added", edge_hash, {
                "src_type": src_type, "src_id": src_id,
                "dst_type": dst_type, "dst_id": dst_id,
                "created_by": created_by
            })

        return edge_hash

    async def get_provenance_edges(
        self,
        src_type: Optional[str] = None,
        src_id: Optional[str] = None,
        dst_type: Optional[str] = None,
        dst_id: Optional[str] = None,
        limit: int = 1000,
        order_by: str = "ts"
    ) -> List[Dict[str, Any]]:
        """Get provenance edges with optional filtering"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        query_parts = ["SELECT * FROM provenance_edges WHERE 1=1"]
        params = []
        param_count = 0

        if src_type:
            param_count += 1
            query_parts.append(f"AND src_type = ${param_count}")
            params.append(src_type)

        if src_id:
            param_count += 1
            query_parts.append(f"AND src_id = ${param_count}")
            params.append(src_id)

        if dst_type:
            param_count += 1
            query_parts.append(f"AND dst_type = ${param_count}")
            params.append(dst_type)

        if dst_id:
            param_count += 1
            query_parts.append(f"AND dst_id = ${param_count}")
            params.append(dst_id)

        # Order by timestamp or created_at
        if order_by == "ts":
            query_parts.append("ORDER BY ts DESC")
        else:
            query_parts.append("ORDER BY created_at DESC")

        param_count += 1
        query_parts.append(f"LIMIT ${param_count}")
        params.append(limit)

        query = " ".join(query_parts)

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            results = []
            for row in rows:
                result = dict(row)
                # Parse meta_json
                if result.get("meta_json"):
                    try:
                        result["meta_data"] = json.loads(result["meta_json"])
                    except json.JSONDecodeError:
                        result["meta_data"] = {}
                results.append(result)
            return results

    async def trace_lineage(
        self,
        entity_type: str,
        entity_id: str,
        direction: str = "upstream",  # upstream, downstream, both
        max_depth: int = 10,
        use_cache: bool = True
    ) -> Dict[str, Any]:
        """Trace lineage for an entity"""
        cache_key = f"lineage_{entity_type}_{entity_id}_{direction}_{max_depth}"

        # Check cache first
        if use_cache:
            cached_result = await self._get_cached_lineage(cache_key)
            if cached_result:
                return cached_result

        lineage_result = await self._compute_lineage(entity_type, entity_id, direction, max_depth)

        # Cache the result
        if use_cache and lineage_result.get("nodes"):
            await self._cache_lineage(cache_key, entity_type, entity_id, direction, lineage_result)

        return lineage_result

    async def _compute_lineage(
        self,
        entity_type: str,
        entity_id: str,
        direction: str,
        max_depth: int
    ) -> Dict[str, Any]:
        """Compute lineage graph traversal"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        visited_entities = set()
        edges_found = []
        nodes = {}

        async def traverse(current_type: str, current_id: str, depth: int):
            if depth > max_depth or (current_type, current_id) in visited_entities:
                return

            visited_entities.add((current_type, current_id))
            nodes[f"{current_type}:{current_id}"] = {
                "type": current_type,
                "id": current_id,
                "depth": depth
            }

            # Build query based on direction
            if direction == "upstream":
                query = "SELECT * FROM provenance_edges WHERE dst_type = $1 AND dst_id = $2"
            elif direction == "downstream":
                query = "SELECT * FROM provenance_edges WHERE src_type = $1 AND src_id = $2"
            else:  # both
                query = """
                    SELECT * FROM provenance_edges
                    WHERE (src_type = $1 AND src_id = $2) OR (dst_type = $1 AND dst_id = $2)
                """

            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query, current_type, current_id)
                for row in rows:
                    edge_data = dict(row)
                    if edge_data.get("meta_json"):
                        try:
                            edge_data["meta_data"] = json.loads(edge_data["meta_json"])
                        except json.JSONDecodeError:
                            edge_data["meta_data"] = {}

                    edges_found.append(edge_data)

                    # Continue traversal
                    if direction == "upstream":
                        next_type, next_id = edge_data["src_type"], edge_data["src_id"]
                    elif direction == "downstream":
                        next_type, next_id = edge_data["dst_type"], edge_data["dst_id"]
                    else:  # both
                        # Add both directions
                        if (edge_data["src_type"], edge_data["src_id"]) != (current_type, current_id):
                            await traverse(edge_data["src_type"], edge_data["src_id"], depth + 1)
                        if (edge_data["dst_type"], edge_data["dst_id"]) != (current_type, current_id):
                            await traverse(edge_data["dst_type"], edge_data["dst_id"], depth + 1)
                        continue

                    await traverse(next_type, next_id, depth + 1)

        await traverse(entity_type, entity_id, 0)

        return {
            "root_entity": {"type": entity_type, "id": entity_id},
            "direction": direction,
            "max_depth": max_depth,
            "nodes": nodes,
            "edges": edges_found,
            "node_count": len(nodes),
            "edge_count": len(edges_found),
            "computed_at": datetime.now(timezone.utc).isoformat()
        }

    def _calculate_edge_hash(self, src_type: str, src_id: str, dst_type: str, dst_id: str, ts: str) -> str:
        """Calculate unique hash for an edge"""
        edge_data = f"{src_type}:{src_id}->{dst_type}:{dst_id}@{ts}"
        return hashlib.sha256(edge_data.encode()).hexdigest()

    # === LINEAGE CACHE ===

    async def _get_cached_lineage(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Get cached lineage result"""
        if not self.pool:
            return None

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT result_json, computed_at, hit_count
                FROM lineage_cache
                WHERE cache_key = $1 AND expires_at > $2
            """, cache_key, datetime.now(timezone.utc).isoformat())

            if row:
                # Update hit count
                await conn.execute(
                    "UPDATE lineage_cache SET hit_count = hit_count + 1 WHERE cache_key = $1",
                    cache_key
                )

                try:
                    result = json.loads(row["result_json"])
                    result["cached"] = True
                    result["cache_hit_count"] = row["hit_count"] + 1
                    return result
                except json.JSONDecodeError:
                    # Invalid cache entry, delete it
                    await conn.execute("DELETE FROM lineage_cache WHERE cache_key = $1", cache_key)

        return None

    async def _cache_lineage(
        self,
        cache_key: str,
        target_type: str,
        target_id: str,
        query_type: str,
        result: Dict[str, Any],
        ttl_hours: int = 24
    ) -> None:
        """Cache lineage result"""
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()

        async with self.transaction() as conn:
            await conn.execute("""
                INSERT INTO lineage_cache (
                    cache_key, target_type, target_id, query_type,
                    result_json, computed_at, expires_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (cache_key) DO UPDATE SET
                    result_json = EXCLUDED.result_json,
                    computed_at = EXCLUDED.computed_at,
                    expires_at = EXCLUDED.expires_at,
                    hit_count = 0
            """,
                cache_key, target_type, target_id, query_type,
                json.dumps(result), datetime.now(timezone.utc).isoformat(), expires_at
            )

    async def cleanup_expired_cache(self) -> int:
        """Clean up expired cache entries"""
        if not self.pool:
            return 0

        async with self.transaction() as conn:
            result = await conn.execute(
                "DELETE FROM lineage_cache WHERE expires_at <= $1",
                datetime.now(timezone.utc).isoformat()
            )
            return int(result.split()[-1]) if result.split()[-1].isdigit() else 0

    # === GRAPH ANALYTICS ===

    async def compute_graph_statistics(self) -> Dict[str, Any]:
        """Compute comprehensive graph statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        stats = {}

        async with self.pool.acquire() as conn:
            # Basic counts
            total_edges = await conn.fetchval("SELECT COUNT(*) FROM provenance_edges")
            stats["total_edges"] = total_edges

            # Entity counts by type
            entity_counts = await conn.fetch("""
                SELECT entity_type, COUNT(DISTINCT entity_id) as count
                FROM (
                    SELECT src_type as entity_type, src_id as entity_id FROM provenance_edges
                    UNION
                    SELECT dst_type as entity_type, dst_id as entity_id FROM provenance_edges
                ) entities
                GROUP BY entity_type
                ORDER BY count DESC
            """)
            stats["entity_counts"] = {row["entity_type"]: row["count"] for row in entity_counts}
            stats["total_entities"] = sum(stats["entity_counts"].values())

            # Edge type distribution
            edge_types = await conn.fetch("""
                SELECT src_type || '->' || dst_type as edge_type, COUNT(*) as count
                FROM provenance_edges
                GROUP BY src_type, dst_type
                ORDER BY count DESC
            """)
            stats["edge_type_distribution"] = {row["edge_type"]: row["count"] for row in edge_types}

            # Temporal analysis
            temporal_info = await conn.fetchrow("""
                SELECT
                    MIN(ts) as earliest_timestamp,
                    MAX(ts) as latest_timestamp,
                    COUNT(DISTINCT DATE(ts::timestamp)) as active_days
                FROM provenance_edges
            """)
            stats["temporal_span"] = dict(temporal_info) if temporal_info else {}

            # Confidence distribution
            confidence_stats = await conn.fetchrow("""
                SELECT
                    AVG(confidence) as avg_confidence,
                    MIN(confidence) as min_confidence,
                    MAX(confidence) as max_confidence,
                    COUNT(*) FILTER (WHERE confidence < 1.0) as low_confidence_count
                FROM provenance_edges
            """)
            stats["confidence_analysis"] = dict(confidence_stats) if confidence_stats else {}

            # Store computed statistics
            await conn.execute("""
                INSERT INTO graph_statistics (
                    computed_at, total_nodes, total_edges, entity_type_distribution_json,
                    temporal_span_days, confidence_stats_json
                ) VALUES ($1, $2, $3, $4, $5, $6)
            """,
                datetime.now(timezone.utc).isoformat(),
                stats["total_entities"],
                stats["total_edges"],
                json.dumps(stats["entity_counts"]),
                temporal_info["active_days"] if temporal_info else 0,
                json.dumps(stats["confidence_analysis"])
            )

        stats["computed_at"] = datetime.now(timezone.utc).isoformat()
        return stats

    async def get_recent_graph_statistics(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent graph statistics"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM graph_statistics
                ORDER BY computed_at DESC
                LIMIT $1
            """, limit)

            results = []
            for row in rows:
                result = dict(row)
                # Parse JSON fields
                for json_field in ["entity_type_distribution_json", "confidence_stats_json"]:
                    if result.get(json_field):
                        try:
                            key = json_field.replace("_json", "")
                            result[key] = json.loads(result[json_field])
                        except json.JSONDecodeError:
                            result[key] = {}
                results.append(result)

            return results

    # === GRAPH VALIDATION ===

    async def validate_graph_integrity(self) -> Dict[str, Any]:
        """Validate graph integrity and consistency"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        validation_start = datetime.now(timezone.utc)
        validation = {
            "valid": True,
            "checks_performed": [],
            "errors": [],
            "warnings": [],
            "started_at": validation_start.isoformat()
        }

        try:
            async with self.pool.acquire() as conn:
                # Check for duplicate edges
                duplicate_check = await conn.fetchrow("""
                    SELECT edge_hash, COUNT(*) as count
                    FROM provenance_edges
                    GROUP BY edge_hash
                    HAVING COUNT(*) > 1
                    LIMIT 1
                """)

                if duplicate_check:
                    validation["errors"].append(f"Duplicate edge hash found: {duplicate_check['edge_hash']}")
                    validation["valid"] = False

                validation["checks_performed"].append("duplicate_edges")

                # Check for invalid timestamps
                invalid_timestamps = await conn.fetchval("""
                    SELECT COUNT(*) FROM provenance_edges
                    WHERE ts IS NULL OR ts = '' OR ts::timestamp IS NULL
                """)

                if invalid_timestamps > 0:
                    validation["errors"].append(f"Found {invalid_timestamps} edges with invalid timestamps")
                    validation["valid"] = False

                validation["checks_performed"].append("timestamp_validation")

                # Check for orphaned entities (entities referenced but not defined)
                # This is informational rather than an error for graph databases
                entity_references = await conn.fetch("""
                    WITH all_entities AS (
                        SELECT src_type as entity_type, src_id as entity_id FROM provenance_edges
                        UNION
                        SELECT dst_type as entity_type, dst_id as entity_id FROM provenance_edges
                    ),
                    entity_counts AS (
                        SELECT entity_type, entity_id, COUNT(*) as reference_count
                        FROM all_entities
                        GROUP BY entity_type, entity_id
                    )
                    SELECT entity_type, COUNT(*) as count
                    FROM entity_counts
                    WHERE reference_count = 1
                    GROUP BY entity_type
                    ORDER BY count DESC
                """)

                orphan_info = {row["entity_type"]: row["count"] for row in entity_references}
                if orphan_info:
                    validation["warnings"].append(f"Potential orphaned entities: {orphan_info}")

                validation["checks_performed"].append("orphaned_entities")

                # Check confidence values
                invalid_confidence = await conn.fetchval("""
                    SELECT COUNT(*) FROM provenance_edges
                    WHERE confidence < 0 OR confidence > 1
                """)

                if invalid_confidence > 0:
                    validation["errors"].append(f"Found {invalid_confidence} edges with invalid confidence values")
                    validation["valid"] = False

                validation["checks_performed"].append("confidence_validation")

                # Store validation results
                validation_duration = (datetime.now(timezone.utc) - validation_start).total_seconds() * 1000
                await conn.execute("""
                    INSERT INTO graph_validation_results (
                        validated_at, validation_type, status, issues_found,
                        issues_json, validation_duration_ms
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                """,
                    validation_start.isoformat(),
                    "integrity_check",
                    "passed" if validation["valid"] else "failed",
                    len(validation["errors"]),
                    json.dumps({"errors": validation["errors"], "warnings": validation["warnings"]}),
                    int(validation_duration)
                )

        except Exception as e:
            validation["valid"] = False
            validation["errors"].append(f"Validation failed: {e}")

        validation["completed_at"] = datetime.now(timezone.utc).isoformat()
        return validation

    # === MIGRATION SUPPORT ===

    async def migrate_from_sqlite(self, sqlite_path: str) -> Dict[str, Any]:
        """Migrate provenance data from SQLite database"""
        migration_result = {
            "status": "started",
            "edges_migrated": 0,
            "errors": [],
            "started_at": datetime.now(timezone.utc).isoformat()
        }

        try:
            import sqlite3

            # Connect to SQLite database
            sqlite_conn = sqlite3.connect(sqlite_path)
            sqlite_conn.row_factory = sqlite3.Row
            cursor = sqlite_conn.cursor()

            # Get all edges from SQLite
            cursor.execute("SELECT src_type, src_id, dst_type, dst_id, ts, meta_json FROM edges ORDER BY ts")
            sqlite_edges = cursor.fetchall()

            if not sqlite_edges:
                migration_result["status"] = "completed"
                migration_result["message"] = "No edges found in source database"
                return migration_result

            # Migrate edges to PostgreSQL
            edge_hashes = set()
            for edge_data in sqlite_edges:
                try:
                    src_type, src_id, dst_type, dst_id, ts, meta_json = edge_data

                    # Skip if invalid
                    if not all([src_type, src_id, dst_type, dst_id, ts]):
                        continue

                    edge_hash = self._calculate_edge_hash(src_type, src_id, dst_type, dst_id, ts)

                    # Skip duplicates
                    if edge_hash in edge_hashes:
                        continue
                    edge_hashes.add(edge_hash)

                    # Parse metadata
                    try:
                        meta_data = json.loads(meta_json) if meta_json else {}
                    except json.JSONDecodeError:
                        meta_data = {}

                    await self.add_provenance_edge(
                        src_type=src_type,
                        src_id=src_id,
                        dst_type=dst_type,
                        dst_id=dst_id,
                        timestamp=ts,
                        meta_data=meta_data,
                        created_by="sqlite_migration"
                    )

                    migration_result["edges_migrated"] += 1

                except Exception as e:
                    error_msg = f"Error migrating edge {edge_data}: {e}"
                    migration_result["errors"].append(error_msg)
                    logger.error(error_msg)

            sqlite_conn.close()
            migration_result["status"] = "completed"

        except Exception as e:
            error_msg = f"Migration failed: {e}"
            migration_result["errors"].append(error_msg)
            migration_result["status"] = "failed"
            logger.error(error_msg)

        migration_result["completed_at"] = datetime.now(timezone.utc).isoformat()
        return migration_result

    # === AUDIT LOGGING ===

    async def _log_audit_operation(
        self,
        conn,
        operation: str,
        target_id: Optional[str],
        data: Dict[str, Any],
        correlation_id: Optional[str] = None
    ) -> None:
        """Log operation to audit trail"""
        await conn.execute("""
            INSERT INTO provenance_audit_log (
                operation, target_id, operation_data_json, performed_at,
                correlation_id, source_service
            ) VALUES ($1, $2, $3, $4, $5, $6)
        """,
            operation, target_id, json.dumps(data),
            datetime.now(timezone.utc).isoformat(),
            correlation_id, "provenance-service"
        )

    async def get_audit_log(
        self,
        operation: Optional[str] = None,
        hours: int = 24,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """Get audit log entries"""
        if not self.pool:
            raise RuntimeError("Database pool not initialized")

        cutoff_time = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        if operation:
            query = """
                SELECT * FROM provenance_audit_log
                WHERE performed_at >= $1 AND operation = $2
                ORDER BY performed_at DESC
                LIMIT $3
            """
            params = [cutoff_time, operation, limit]
        else:
            query = """
                SELECT * FROM provenance_audit_log
                WHERE performed_at >= $1
                ORDER BY performed_at DESC
                LIMIT $2
            """
            params = [cutoff_time, limit]

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            results = []
            for row in rows:
                result = dict(row)
                if result.get("operation_data_json"):
                    try:
                        result["operation_data"] = json.loads(result["operation_data_json"])
                    except json.JSONDecodeError:
                        result["operation_data"] = {}
                results.append(result)
            return results

    # === CLEANUP AND MAINTENANCE ===

    async def cleanup_old_data(self, days: int = 90) -> Dict[str, int]:
        """Clean up old provenance data"""
        cutoff_time = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        async with self.transaction() as conn:
            # Clean old graph statistics
            stats_result = await conn.execute(
                "DELETE FROM graph_statistics WHERE computed_at < $1", cutoff_time
            )
            stats_deleted = int(stats_result.split()[-1]) if stats_result.split()[-1].isdigit() else 0

            # Clean old validation results
            validation_result = await conn.execute(
                "DELETE FROM graph_validation_results WHERE validated_at < $1", cutoff_time
            )
            validation_deleted = int(validation_result.split()[-1]) if validation_result.split()[-1].isdigit() else 0

            # Clean old audit log
            audit_result = await conn.execute(
                "DELETE FROM provenance_audit_log WHERE performed_at < $1", cutoff_time
            )
            audit_deleted = int(audit_result.split()[-1]) if audit_result.split()[-1].isdigit() else 0

            # Clean expired cache
            cache_deleted = await self.cleanup_expired_cache()

            return {
                "graph_statistics_deleted": stats_deleted,
                "validation_results_deleted": validation_deleted,
                "audit_log_deleted": audit_deleted,
                "cache_entries_deleted": cache_deleted
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
                edges_count = await conn.fetchval("SELECT COUNT(*) FROM provenance_edges")
                cache_count = await conn.fetchval("SELECT COUNT(*) FROM lineage_cache")
                stats_count = await conn.fetchval("SELECT COUNT(*) FROM graph_statistics")
                audit_count = await conn.fetchval("SELECT COUNT(*) FROM provenance_audit_log")

                # Get recent activity
                recent_edges = await conn.fetchval(
                    "SELECT COUNT(*) FROM provenance_edges WHERE created_at >= $1",
                    (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
                )

                # Cache hit rate
                cache_stats = await conn.fetchrow("""
                    SELECT
                        COUNT(*) as total_entries,
                        SUM(hit_count) as total_hits,
                        COUNT(*) FILTER (WHERE expires_at <= $1) as expired_entries
                    FROM lineage_cache
                """, datetime.now(timezone.utc).isoformat())

                return {
                    "status": "healthy" if result == 1 else "unhealthy",
                    "pool_size": self.pool.get_size(),
                    "provenance_edges": edges_count,
                    "lineage_cache_entries": cache_count,
                    "graph_statistics": stats_count,
                    "audit_log_entries": audit_count,
                    "recent_edges_1h": recent_edges,
                    "cache_hit_rate": (cache_stats["total_hits"] / max(cache_stats["total_entries"], 1)) if cache_stats else 0,
                    "expired_cache_entries": cache_stats["expired_entries"] if cache_stats else 0,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }


# Global store instance
provenance_store: Optional[PostgreSQLProvenanceStore] = None


async def get_provenance_store() -> PostgreSQLProvenanceStore:
    """Get the global provenance store instance"""
    global provenance_store
    if provenance_store is None:
        raise RuntimeError("Provenance store not initialized")
    return provenance_store


async def initialize_provenance_store(database_url: str):
    """Initialize the global provenance store"""
    global provenance_store
    provenance_store = PostgreSQLProvenanceStore(database_url)
    await provenance_store.initialize()


async def close_provenance_store():
    """Close the global provenance store"""
    global provenance_store
    if provenance_store:
        await provenance_store.close()
        provenance_store = None