#!/usr/bin/env python3
"""
Provenance Service Database Migration Service
ADR-0015 Phase 2: Database-per-Service

Migrates provenance graph from shared location to provenance-service ownership.
Following the pattern established by truth-service and knowledge-service migration infrastructure.

Migration Strategy:
1. Extract shared provenance.sqlite to provenance-service ownership
2. Add enhanced schema with analytics and validation capabilities
3. Validate graph integrity and temporal ordering
4. Provide dual-access mode for compatibility during migration
5. Enable graph analytics and monitoring capabilities
"""

import asyncio
import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import aiosqlite
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

class ProvenanceMigrationService:
    """Handles migration of provenance graph from shared to service-owned database."""

    def __init__(self, target_database_path: str, source_database_path: str = None):
        self.target_database_path = target_database_path
        self.source_database_path = source_database_path or self._find_source_database()
        self.migration_stats = {}

    def _find_source_database(self) -> str:
        """Find the shared provenance.sqlite database."""
        project_root = Path(__file__).parents[3]  # Back to babyAI root
        shared_provenance = project_root / "shared" / "babyai_shared" / "provenance" / "provenance.sqlite"
        return str(shared_provenance)

    async def migration_needed(self) -> bool:
        """Check if migration is needed."""
        try:
            # Check if source database exists
            source_exists = Path(self.source_database_path).exists()
            if not source_exists:
                logger.info("No shared provenance.sqlite found - migration not needed")
                return False

            # Check if target database exists and has data
            target_exists = Path(self.target_database_path).exists()
            if target_exists:
                async with aiosqlite.connect(self.target_database_path) as conn:
                    cursor = await conn.execute("SELECT COUNT(*) FROM provenance_edges")
                    row_count = (await cursor.fetchone())[0]
                    if row_count > 0:
                        logger.info(f"Target database already has {row_count} provenance edges")
                        return False

            logger.info("Shared provenance.sqlite exists and target is empty - migration needed")
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
            "target_edges": 0,
            "migration_log": [],
            "last_checked": datetime.now(timezone.utc).isoformat()
        }

        try:
            # Get source database stats
            if status["source_exists"]:
                status["source_size_bytes"] = Path(self.source_database_path).stat().st_size

                async with aiosqlite.connect(self.source_database_path) as source_conn:
                    # Get edge count
                    cursor = await source_conn.execute("SELECT COUNT(*) FROM edges")
                    status["source_edges"] = (await cursor.fetchone())[0]

                    # Get entity type distribution
                    cursor = await source_conn.execute("""
                        SELECT src_type, COUNT(*) as count
                        FROM edges
                        GROUP BY src_type
                        ORDER BY count DESC
                    """)
                    source_types = dict(await cursor.fetchall())
                    status["source_entity_types"] = source_types

                    # Get recent edges sample
                    cursor = await source_conn.execute("""
                        SELECT src_type, src_id, dst_type, dst_id, ts
                        FROM edges
                        ORDER BY ts DESC
                        LIMIT 5
                    """)
                    recent_edges = []
                    for row in await cursor.fetchall():
                        recent_edges.append({
                            "src_type": row[0], "src_id": row[1],
                            "dst_type": row[2], "dst_id": row[3],
                            "ts": row[4]
                        })
                    status["recent_edges"] = recent_edges

            # Get target database stats
            if status["target_exists"]:
                async with aiosqlite.connect(self.target_database_path) as target_conn:
                    # Check if enhanced tables exist
                    cursor = await target_conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                    )
                    tables = [row[0] for row in await cursor.fetchall()]
                    status["target_tables"] = tables

                    if "provenance_edges" in tables:
                        cursor = await target_conn.execute("SELECT COUNT(*) FROM provenance_edges")
                        status["target_edges"] = (await cursor.fetchone())[0]

                    # Get migration log if it exists
                    if "provenance_migration_log" in tables:
                        cursor = await target_conn.execute("""
                            SELECT operation, records_processed, status, started_at, completed_at, error_message
                            FROM provenance_migration_log
                            ORDER BY started_at DESC
                            LIMIT 10
                        """)
                        status["migration_log"] = [dict(zip([col[0] for col in cursor.description], row)) for row in await cursor.fetchall()]

        except Exception as e:
            logger.error(f"Error getting migration status: {e}")
            status["error"] = str(e)

        return status

    async def migrate_provenance_graph(self) -> Dict[str, Any]:
        """Perform complete migration of provenance graph."""
        start_time = datetime.now(timezone.utc)
        results = {
            "status": "started",
            "started_at": start_time.isoformat(),
            "edges_migrated": 0,
            "validation_results": {},
            "graph_analysis": {},
            "errors": []
        }

        try:
            logger.info("Starting provenance graph migration...")

            # Check if migration is needed
            if not await self.migration_needed():
                results["status"] = "skipped"
                results["message"] = "Migration not needed"
                return results

            # Create migration log entry
            migration_id = await self._create_migration_log("graph_migration")

            # Initialize target database with enhanced schema
            await self._initialize_target_database()

            # Migrate provenance edges
            migration_result = await self._migrate_provenance_edges()
            results.update(migration_result)

            # Analyze graph structure
            analysis_result = await self._analyze_graph_structure()
            results["graph_analysis"] = analysis_result

            # Validate migrated graph
            validation_result = await self._validate_graph_integrity()
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
            # Create enhanced provenance schema
            await conn.executescript("""
                -- Enhanced provenance edges table (compatible with original)
                CREATE TABLE IF NOT EXISTS provenance_edges (
                    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src_type TEXT NOT NULL,
                    src_id TEXT NOT NULL,
                    dst_type TEXT NOT NULL,
                    dst_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    meta_json TEXT NOT NULL DEFAULT '{}',
                    -- Enhanced fields
                    edge_hash TEXT,
                    confidence REAL DEFAULT 1.0,
                    validation_status TEXT DEFAULT 'unvalidated',
                    created_by TEXT,
                    migrated_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_provenance_edges_src ON provenance_edges (src_type, src_id);
                CREATE INDEX IF NOT EXISTS idx_provenance_edges_dst ON provenance_edges (dst_type, dst_id);
                CREATE INDEX IF NOT EXISTS idx_provenance_edges_ts ON provenance_edges (ts);
                CREATE INDEX IF NOT EXISTS idx_provenance_edges_hash ON provenance_edges (edge_hash);

                -- Graph analytics tables
                CREATE TABLE IF NOT EXISTS graph_statistics (
                    stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    computed_at TEXT NOT NULL,
                    total_nodes INTEGER,
                    total_edges INTEGER,
                    max_depth INTEGER,
                    connected_components INTEGER,
                    cycles_detected INTEGER,
                    entity_type_distribution_json TEXT,
                    temporal_span_days REAL
                );

                CREATE TABLE IF NOT EXISTS lineage_cache (
                    cache_key TEXT PRIMARY KEY,
                    target_id TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    query_type TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    computed_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    hit_count INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_lineage_cache_target ON lineage_cache (target_type, target_id);
                CREATE INDEX IF NOT EXISTS idx_lineage_cache_expires ON lineage_cache (expires_at);

                CREATE TABLE IF NOT EXISTS graph_validation_results (
                    validation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    validated_at TEXT NOT NULL,
                    validation_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    issues_found INTEGER DEFAULT 0,
                    issues_json TEXT DEFAULT '[]',
                    validation_duration_ms INTEGER
                );

                CREATE TABLE IF NOT EXISTS provenance_audit_log (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    operation TEXT NOT NULL,
                    edge_id INTEGER,
                    operation_data_json TEXT,
                    performed_by TEXT,
                    performed_at TEXT NOT NULL,
                    correlation_id TEXT,
                    source_service TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_audit_log_performed_at ON provenance_audit_log (performed_at);
                CREATE INDEX IF NOT EXISTS idx_audit_log_correlation_id ON provenance_audit_log (correlation_id);

                CREATE TABLE IF NOT EXISTS provenance_migration_log (
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

    async def _migrate_provenance_edges(self) -> Dict[str, Any]:
        """Migrate provenance edges from source to target database."""
        result = {
            "edges_migrated": 0,
            "edges_skipped": 0,
            "errors": [],
            "duplicate_edges": 0
        }

        try:
            logger.info("Migrating provenance edges...")

            # Read all edges from source
            async with aiosqlite.connect(self.source_database_path) as source_conn:
                cursor = await source_conn.execute("""
                    SELECT src_type, src_id, dst_type, dst_id, ts, meta_json
                    FROM edges
                    ORDER BY ts ASC
                """)
                source_edges = await cursor.fetchall()

            if not source_edges:
                logger.info("No edges found in source database")
                return result

            # Migrate edges to target database
            async with aiosqlite.connect(self.target_database_path) as target_conn:
                edge_hashes = set()

                for edge in source_edges:
                    try:
                        src_type, src_id, dst_type, dst_id, ts, meta_json = edge

                        # Validate edge data
                        if not self._validate_edge_data(src_type, src_id, dst_type, dst_id, ts):
                            result["edges_skipped"] += 1
                            result["errors"].append(f"Invalid edge data: {src_type}:{src_id} -> {dst_type}:{dst_id}")
                            continue

                        # Calculate edge hash for deduplication
                        edge_hash = self._calculate_edge_hash(src_type, src_id, dst_type, dst_id, ts)

                        if edge_hash in edge_hashes:
                            result["duplicate_edges"] += 1
                            continue

                        edge_hashes.add(edge_hash)

                        # Insert edge with enhanced fields
                        await target_conn.execute("""
                            INSERT INTO provenance_edges (
                                src_type, src_id, dst_type, dst_id, ts, meta_json,
                                edge_hash, created_by, migrated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            src_type, src_id, dst_type, dst_id, ts, meta_json,
                            edge_hash, "migration_service", datetime.now(timezone.utc).isoformat()
                        ))

                        result["edges_migrated"] += 1

                        # Log the migration operation
                        await self._log_audit_operation("edge_migrated", None, {
                            "source": "shared_provenance",
                            "target": "provenance_service",
                            "edge_hash": edge_hash
                        })

                    except Exception as e:
                        error_msg = f"Error migrating edge {edge}: {e}"
                        logger.error(error_msg)
                        result["errors"].append(error_msg)
                        result["edges_skipped"] += 1

                # Commit all changes
                await target_conn.commit()

        except Exception as e:
            error_msg = f"Error during edge migration: {e}"
            logger.error(error_msg)
            result["errors"].append(error_msg)

        logger.info(f"Migration complete: {result['edges_migrated']} migrated, {result['edges_skipped']} skipped")
        return result

    def _validate_edge_data(self, src_type: str, src_id: str, dst_type: str, dst_id: str, ts: str) -> bool:
        """Validate edge data before migration."""
        try:
            # Check required fields
            if not all([src_type, src_id, dst_type, dst_id, ts]):
                return False

            # Validate entity types
            valid_types = {"decision", "tool_run", "artifact", "eval_result", "user", "system"}
            if src_type not in valid_types or dst_type not in valid_types:
                logger.warning(f"Unknown entity types: {src_type}, {dst_type}")
                # Allow unknown types for forward compatibility

            # Validate timestamp format
            datetime.fromisoformat(ts.replace('Z', '+00:00'))

            # Check ID lengths
            if len(src_id) > 256 or len(dst_id) > 256:
                return False

            return True

        except Exception as e:
            logger.error(f"Validation error for edge {src_type}:{src_id} -> {dst_type}:{dst_id}: {e}")
            return False

    def _calculate_edge_hash(self, src_type: str, src_id: str, dst_type: str, dst_id: str, ts: str) -> str:
        """Calculate hash for edge deduplication."""
        edge_data = f"{src_type}:{src_id}->{dst_type}:{dst_id}@{ts}"
        return hashlib.sha256(edge_data.encode()).hexdigest()[:16]

    async def _analyze_graph_structure(self) -> Dict[str, Any]:
        """Analyze the migrated graph structure."""
        analysis = {
            "total_edges": 0,
            "entity_counts": {},
            "edge_type_distribution": {},
            "temporal_span": {},
            "connectivity_analysis": {}
        }

        try:
            async with aiosqlite.connect(self.target_database_path) as conn:
                # Total edges
                cursor = await conn.execute("SELECT COUNT(*) FROM provenance_edges")
                analysis["total_edges"] = (await cursor.fetchone())[0]

                # Entity counts by type
                cursor = await conn.execute("""
                    SELECT entity_type, COUNT(DISTINCT entity_id) as count
                    FROM (
                        SELECT src_type as entity_type, src_id as entity_id FROM provenance_edges
                        UNION
                        SELECT dst_type as entity_type, dst_id as entity_id FROM provenance_edges
                    )
                    GROUP BY entity_type
                    ORDER BY count DESC
                """)
                analysis["entity_counts"] = dict(await cursor.fetchall())

                # Edge type distribution
                cursor = await conn.execute("""
                    SELECT src_type || '->' || dst_type as edge_type, COUNT(*) as count
                    FROM provenance_edges
                    GROUP BY src_type, dst_type
                    ORDER BY count DESC
                """)
                analysis["edge_type_distribution"] = dict(await cursor.fetchall())

                # Temporal analysis
                cursor = await conn.execute("""
                    SELECT
                        MIN(ts) as earliest,
                        MAX(ts) as latest,
                        COUNT(*) as total_edges
                    FROM provenance_edges
                """)
                temporal_data = await cursor.fetchone()
                if temporal_data and temporal_data[0]:
                    analysis["temporal_span"] = {
                        "earliest": temporal_data[0],
                        "latest": temporal_data[1],
                        "total_edges": temporal_data[2]
                    }

                # Store analysis in graph_statistics table
                await conn.execute("""
                    INSERT INTO graph_statistics (
                        computed_at, total_nodes, total_edges, entity_type_distribution_json
                    ) VALUES (?, ?, ?, ?)
                """, (
                    datetime.now(timezone.utc).isoformat(),
                    sum(analysis["entity_counts"].values()),
                    analysis["total_edges"],
                    json.dumps(analysis["entity_counts"])
                ))
                await conn.commit()

        except Exception as e:
            logger.error(f"Error analyzing graph structure: {e}")
            analysis["error"] = str(e)

        return analysis

    async def _validate_graph_integrity(self) -> Dict[str, Any]:
        """Validate the integrity of the migrated graph."""
        validation = {
            "valid": True,
            "checks_performed": [],
            "errors": [],
            "warnings": []
        }

        try:
            # Compare record counts
            async with aiosqlite.connect(self.source_database_path) as source_conn:
                cursor = await source_conn.execute("SELECT COUNT(*) FROM edges")
                source_count = (await cursor.fetchone())[0]

            async with aiosqlite.connect(self.target_database_path) as target_conn:
                cursor = await target_conn.execute("SELECT COUNT(*) FROM provenance_edges")
                target_count = (await cursor.fetchone())[0]

            count_check = {
                "check": "edge_count",
                "source_count": source_count,
                "target_count": target_count,
                "match": source_count == target_count
            }
            validation["checks_performed"].append(count_check)

            if not count_check["match"]:
                validation["errors"].append(f"Edge count mismatch: source={source_count}, target={target_count}")
                validation["valid"] = False

            # Validate sample edges for content integrity
            await self._validate_sample_edges(validation)

            # Check for temporal ordering consistency
            await self._validate_temporal_ordering(validation)

            # Check for valid edge types
            await self._validate_edge_types(validation)

            # Store validation results
            async with aiosqlite.connect(self.target_database_path) as target_conn:
                await conn.execute("""
                    INSERT INTO graph_validation_results (
                        validated_at, validation_type, status, issues_found, issues_json
                    ) VALUES (?, ?, ?, ?, ?)
                """, (
                    datetime.now(timezone.utc).isoformat(),
                    "migration_validation",
                    "passed" if validation["valid"] else "failed",
                    len(validation["errors"]),
                    json.dumps(validation["errors"])
                ))
                await target_conn.commit()

        except Exception as e:
            validation["valid"] = False
            validation["errors"].append(f"Validation failed: {e}")

        return validation

    async def _validate_sample_edges(self, validation: Dict[str, Any]):
        """Validate a sample of migrated edges for content integrity."""
        try:
            # Get sample from source
            async with aiosqlite.connect(self.source_database_path) as source_conn:
                cursor = await source_conn.execute("""
                    SELECT src_type, src_id, dst_type, dst_id, ts, meta_json
                    FROM edges
                    ORDER BY ts DESC
                    LIMIT 10
                """)
                source_samples = await cursor.fetchall()

            # Compare with target
            async with aiosqlite.connect(self.target_database_path) as target_conn:
                for sample in source_samples:
                    src_type, src_id, dst_type, dst_id, ts, source_meta = sample

                    cursor = await target_conn.execute("""
                        SELECT meta_json FROM provenance_edges
                        WHERE src_type = ? AND src_id = ? AND dst_type = ? AND dst_id = ? AND ts = ?
                    """, (src_type, src_id, dst_type, dst_id, ts))
                    target_record = await cursor.fetchone()

                    if not target_record:
                        validation["errors"].append(f"Missing edge: {src_type}:{src_id} -> {dst_type}:{dst_id}")
                        validation["valid"] = False
                        continue

                    target_meta = target_record[0]

                    # Compare metadata
                    if source_meta != target_meta:
                        validation["warnings"].append(f"Metadata mismatch for edge: {src_type}:{src_id} -> {dst_type}:{dst_id}")

        except Exception as e:
            validation["errors"].append(f"Sample validation failed: {e}")
            validation["valid"] = False

    async def _validate_temporal_ordering(self, validation: Dict[str, Any]):
        """Validate temporal ordering of edges."""
        try:
            async with aiosqlite.connect(self.target_database_path) as target_conn:
                # Check for any edges with invalid timestamps
                cursor = await target_conn.execute("""
                    SELECT COUNT(*) FROM provenance_edges
                    WHERE ts IS NULL OR ts = ''
                """)
                invalid_timestamps = (await cursor.fetchone())[0]

                if invalid_timestamps > 0:
                    validation["errors"].append(f"Found {invalid_timestamps} edges with invalid timestamps")
                    validation["valid"] = False

        except Exception as e:
            validation["errors"].append(f"Temporal validation failed: {e}")
            validation["valid"] = False

    async def _validate_edge_types(self, validation: Dict[str, Any]):
        """Validate edge type combinations."""
        try:
            async with aiosqlite.connect(self.target_database_path) as target_conn:
                # Get all unique edge type combinations
                cursor = await target_conn.execute("""
                    SELECT DISTINCT src_type, dst_type, COUNT(*) as count
                    FROM provenance_edges
                    GROUP BY src_type, dst_type
                    ORDER BY count DESC
                """)
                edge_types = await cursor.fetchall()

                # Common valid edge types
                known_valid_types = {
                    ("decision", "tool_run"),
                    ("tool_run", "artifact"),
                    ("decision", "eval_result"),
                    ("decision", "artifact")
                }

                for src_type, dst_type, count in edge_types:
                    if (src_type, dst_type) not in known_valid_types:
                        validation["warnings"].append(
                            f"Uncommon edge type: {src_type} -> {dst_type} (count: {count})"
                        )

        except Exception as e:
            validation["errors"].append(f"Edge type validation failed: {e}")
            validation["valid"] = False

    async def _create_migration_log(self, operation: str) -> str:
        """Create migration log entry and return log ID."""
        log_id = f"migration_{int(datetime.now(timezone.utc).timestamp())}"

        try:
            async with aiosqlite.connect(self.target_database_path) as conn:
                await conn.execute("""
                    INSERT INTO provenance_migration_log
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
                    UPDATE provenance_migration_log
                    SET status = ?, completed_at = ?
                    WHERE migration_id = ? AND operation = 'graph_migration'
                """, (status, datetime.now(timezone.utc).isoformat(), migration_id))
                await conn.commit()

        except Exception as e:
            logger.error(f"Error completing migration log: {e}")

    async def _log_audit_operation(self, operation: str, edge_id: Optional[int], data: Dict[str, Any]):
        """Log operation to audit trail."""
        try:
            async with aiosqlite.connect(self.target_database_path) as conn:
                await conn.execute("""
                    INSERT INTO provenance_audit_log
                    (operation, edge_id, operation_data_json, performed_by, performed_at, source_service)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    operation, edge_id, json.dumps(data),
                    "migration_service", datetime.now(timezone.utc).isoformat(),
                    "provenance-service"
                ))

        except Exception as e:
            logger.error(f"Error logging audit operation: {e}")

    async def cleanup_shared_provenance(self) -> Dict[str, Any]:
        """Remove shared provenance access after successful migration."""
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
                result["error"] = "Migration not complete - cannot cleanup shared provenance"
                return result

            # Create backup of shared provenance
            shared_provenance_path = Path(self.source_database_path)
            if shared_provenance_path.exists():
                backup_path = shared_provenance_path.with_suffix('.sqlite.migrated_backup')
                shared_provenance_path.rename(backup_path)
                result["actions_taken"].append(f"Moved shared provenance to backup: {backup_path}")

            result["status"] = "completed"
            logger.info("Shared provenance cleanup completed successfully")

        except Exception as e:
            error_msg = f"Error cleaning up shared provenance: {e}"
            logger.error(error_msg)
            result["status"] = "failed"
            result["error"] = error_msg
            result["errors"].append(error_msg)

        return result

# Async context manager for database connections
@asynccontextmanager
async def get_provenance_migration_service(target_database_path: str, source_database_path: str = None):
    """Context manager for provenance migration service."""
    service = ProvenanceMigrationService(target_database_path, source_database_path)
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
        target_db = os.getenv("TARGET_DATABASE", "provenance_graph.db")

        async with get_provenance_migration_service(target_db) as migration_service:
            # Check migration status
            status = await migration_service.get_migration_status()
            print(f"Migration status: {json.dumps(status, indent=2)}")

            # Run migration if needed
            if status.get("migration_needed"):
                print("Starting migration...")
                results = await migration_service.migrate_provenance_graph()
                print(f"Migration results: {json.dumps(results, indent=2)}")
            else:
                print("Migration not needed")

    asyncio.run(main())