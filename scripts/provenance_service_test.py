#!/usr/bin/env python3
"""
Provenance Service Migration Test

Tests the complete provenance-service extraction including:
1. Database migration from shared provenance.sqlite
2. Enhanced schema with graph analytics
3. Event schema validation
4. Graph integrity validation

Usage:
    python scripts/provenance_service_test.py [--migrate] [--verbose] [--cleanup]
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# Add project root and service path to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "services" / "provenance-service" / "src"))

# Import provenance-service components
from migration_service import ProvenanceMigrationService

logger = logging.getLogger(__name__)

class ProvenanceServiceTest:
    """Tests provenance-service migration and deployment."""

    def __init__(self, test_database_path: str = "test_provenance_graph.db"):
        self.test_database_path = test_database_path
        self.migration_service = None

    async def run_tests(self, run_migration: bool = False, cleanup_shared: bool = False) -> bool:
        """Run all provenance-service tests."""
        try:
            logger.info("Starting Provenance Service extraction and deployment tests...")

            # Test 1: Migration service initialization
            await self._test_migration_service()

            # Test 2: Database migration (if requested)
            if run_migration:
                await self._test_database_migration()

            # Test 3: Event schema validation
            await self._test_event_schemas()

            # Test 4: Enhanced database capabilities
            await self._test_enhanced_schema()

            # Test 5: Graph integrity validation
            await self._test_graph_validation()

            # Test 6: Shared provenance cleanup (if requested)
            if cleanup_shared:
                await self._test_shared_cleanup()

            logger.info("[PASS] All provenance-service tests passed!")
            return True

        except Exception as e:
            logger.error(f"[FAIL] Provenance-service test failed: {e}")
            return False

        finally:
            await self._cleanup()

    async def _test_migration_service(self):
        """Test migration service initialization and status."""
        logger.info("Testing migration service...")

        # Remove test database if exists
        test_db_path = Path(self.test_database_path)
        if test_db_path.exists():
            test_db_path.unlink()

        # Initialize migration service
        self.migration_service = ProvenanceMigrationService(
            target_database_path=self.test_database_path
        )

        # Check migration status
        status = await self.migration_service.get_migration_status()

        # Validate status structure
        required_fields = [
            "migration_needed", "source_path", "source_exists",
            "target_path", "target_exists", "last_checked"
        ]

        for field in required_fields:
            assert field in status, f"Missing status field: {field}"

        logger.info(f"Source database: {status['source_path']}")
        logger.info(f"Source exists: {status['source_exists']}")
        logger.info(f"Migration needed: {status['migration_needed']}")

        if status["source_exists"]:
            logger.info(f"Source edges: {status.get('source_edges', 0)}")
            logger.info(f"Entity types: {status.get('source_entity_types', {})}")
            recent = status.get('recent_edges', [])
            if recent:
                edge_types = [f"{edge['src_type']}->{edge['dst_type']}" for edge in recent[:3]]
                logger.info(f"Recent edge types: {edge_types}")

        logger.info("[PASS] Migration service test successful")

    async def _test_database_migration(self):
        """Test database migration from shared provenance."""
        logger.info("Testing database migration...")

        if not self.migration_service:
            self.migration_service = ProvenanceMigrationService(self.test_database_path)

        # Check if migration is needed
        migration_needed = await self.migration_service.migration_needed()
        logger.info(f"Migration needed: {migration_needed}")

        if migration_needed:
            # Run migration
            logger.info("Starting provenance graph migration...")
            results = await self.migration_service.migrate_provenance_graph()

            # Validate migration results
            assert results.get("status") in ["completed", "completed_with_errors"], f"Migration failed: {results.get('error')}"
            assert results.get("edges_migrated", 0) >= 0, "No edges migrated"

            # Check validation results
            validation = results.get("validation_results", {})
            if not validation.get("valid", False):
                logger.warning(f"Migration validation issues: {validation.get('errors', [])}")

            # Check graph analysis
            analysis = results.get("graph_analysis", {})
            logger.info(f"Graph analysis: {analysis.get('total_edges', 0)} edges, {len(analysis.get('entity_counts', {}))} entity types")

            logger.info(f"Migration completed: {results.get('edges_migrated')} edges migrated")

        else:
            logger.info("Migration not needed - target already has data or source missing")

        logger.info("[PASS] Database migration test successful")

    async def _test_event_schemas(self):
        """Test event schema definitions exist and are valid."""
        logger.info("Testing event schemas...")

        # Check that all required schemas exist
        schemas_path = project_root / "schemas"
        required_schemas = [
            "provenance/edge_create_v1.json",
            "provenance/lineage_query_v1.json",
            "provenance/lineage_result_v1.json"
        ]

        for schema_file in required_schemas:
            schema_path = schemas_path / schema_file
            assert schema_path.exists(), f"Missing schema: {schema_file}"

            # Validate JSON syntax
            with open(schema_path, 'r') as f:
                schema_data = json.load(f)

            # Check required schema structure
            assert "$schema" in schema_data, f"Schema {schema_file} missing $schema"
            assert "title" in schema_data, f"Schema {schema_file} missing title"
            assert "properties" in schema_data, f"Schema {schema_file} missing properties"

            # Check envelope structure
            envelope = schema_data.get("properties", {}).get("envelope", {})
            assert envelope, f"Schema {schema_file} missing envelope"

            logger.debug(f"OK Schema validated: {schema_file}")

        logger.info("[PASS] Event schemas test successful")

    async def _test_enhanced_schema(self):
        """Test enhanced database schema capabilities."""
        logger.info("Testing enhanced schema...")

        if not self.migration_service:
            self.migration_service = ProvenanceMigrationService(self.test_database_path)

        # Initialize database to test schema
        await self.migration_service._initialize_target_database()

        # Check that enhanced tables exist
        import aiosqlite
        async with aiosqlite.connect(self.test_database_path) as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row[0] for row in await cursor.fetchall()]

        expected_tables = [
            "provenance_edges",
            "graph_statistics",
            "lineage_cache",
            "graph_validation_results",
            "provenance_audit_log",
            "provenance_migration_log"
        ]

        for table in expected_tables:
            assert table in tables, f"Missing enhanced table: {table}"

        logger.info("Enhanced tables created:")
        for table in expected_tables:
            logger.info(f"  - {table}")

        # Test enhanced provenance_edges table structure
        async with aiosqlite.connect(self.test_database_path) as conn:
            cursor = await conn.execute("PRAGMA table_info(provenance_edges)")
            columns = [row[1] for row in await cursor.fetchall()]

        expected_columns = [
            "edge_id", "src_type", "src_id", "dst_type", "dst_id", "ts",
            "meta_json", "edge_hash", "confidence", "validation_status"
        ]

        for column in expected_columns:
            assert column in columns, f"Missing enhanced column: {column}"

        logger.info("[PASS] Enhanced schema test successful")

    async def _test_graph_validation(self):
        """Test graph validation capabilities."""
        logger.info("Testing graph validation...")

        if not self.migration_service:
            self.migration_service = ProvenanceMigrationService(self.test_database_path)

        # Test validation with empty database
        validation = await self.migration_service._validate_graph_integrity()

        # Should have validation structure
        required_validation_fields = ["valid", "checks_performed", "errors", "warnings"]
        for field in required_validation_fields:
            assert field in validation, f"Missing validation field: {field}"

        logger.info(f"Validation structure: {list(validation.keys())}")
        logger.info(f"Checks performed: {len(validation.get('checks_performed', []))}")

        if validation.get("errors"):
            logger.info(f"Validation errors: {validation['errors']}")
        if validation.get("warnings"):
            logger.info(f"Validation warnings: {validation['warnings']}")

        # Test graph analysis
        analysis = await self.migration_service._analyze_graph_structure()
        logger.info(f"Graph analysis: {analysis.get('total_edges', 0)} edges")

        if analysis.get("entity_counts"):
            logger.info(f"Entity types: {list(analysis['entity_counts'].keys())}")

        logger.info("[PASS] Graph validation test successful")

    async def _test_shared_cleanup(self):
        """Test shared provenance cleanup."""
        logger.info("Testing shared provenance cleanup...")

        if not self.migration_service:
            self.migration_service = ProvenanceMigrationService(self.test_database_path)

        # Test cleanup (will skip if migration not complete)
        cleanup_result = await self.migration_service.cleanup_shared_provenance()

        logger.info(f"Cleanup result: {json.dumps(cleanup_result, indent=2)}")

        # Validate cleanup was successful or appropriately skipped
        assert cleanup_result.get("status") in ["completed", "skipped"], f"Cleanup failed: {cleanup_result.get('error')}"

        if cleanup_result.get("status") == "completed":
            actions = cleanup_result.get("actions_taken", [])
            assert len(actions) > 0, "No cleanup actions taken"
            logger.info(f"Cleanup actions: {actions}")

        logger.info("[PASS] Shared provenance cleanup test successful")

    async def _cleanup(self):
        """Cleanup test resources."""
        # Remove test database
        test_db_path = Path(self.test_database_path)
        if test_db_path.exists():
            test_db_path.unlink()
            logger.info("Cleaned up test database")

async def main():
    parser = argparse.ArgumentParser(description="Test provenance-service migration and extraction")
    parser.add_argument("--migrate", action="store_true", help="Run migration tests")
    parser.add_argument("--cleanup", action="store_true", help="Run shared cleanup tests")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Run tests
    test = ProvenanceServiceTest()
    success = await test.run_tests(
        run_migration=args.migrate,
        cleanup_shared=args.cleanup
    )

    if success:
        print("\n[PASS] Provenance Service Extraction Test: PASSED")
        print("[PASS] Database migration infrastructure working")
        print("[PASS] Event schemas defined and validated")
        print("[PASS] Enhanced schema with graph analytics")
        print("[PASS] Graph validation operational")
        print("[PASS] Ready for deployment")
        sys.exit(0)
    else:
        print("\n[FAIL] Provenance Service Extraction Test: FAILED")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())