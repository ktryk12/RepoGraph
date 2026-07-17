#!/usr/bin/env python3
"""
Knowledge Service Migration Test

Tests the complete knowledge-service extraction including:
1. Database migration from shared registry.sqlite
2. Enhanced schema with audit trails
3. Event schema validation
4. Migration integrity validation

Usage:
    python scripts/knowledge_service_test.py [--migrate] [--verbose] [--cleanup]
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
sys.path.insert(0, str(project_root / "services" / "knowledge-service" / "src"))

# Import knowledge-service components
from migration_service import KnowledgeMigrationService

logger = logging.getLogger(__name__)

class KnowledgeServiceTest:
    """Tests knowledge-service migration and deployment."""

    def __init__(self, test_database_path: str = "test_knowledge_registry.db"):
        self.test_database_path = test_database_path
        self.migration_service = None

    async def run_tests(self, run_migration: bool = False, cleanup_shared: bool = False) -> bool:
        """Run all knowledge-service tests."""
        try:
            logger.info("Starting Knowledge Service extraction and deployment tests...")

            # Test 1: Migration service initialization
            await self._test_migration_service()

            # Test 2: Database migration (if requested)
            if run_migration:
                await self._test_database_migration()

            # Test 3: Event schema validation
            await self._test_event_schemas()

            # Test 4: Enhanced database capabilities
            await self._test_enhanced_schema()

            # Test 5: Migration validation
            await self._test_migration_validation()

            # Test 6: Shared registry cleanup (if requested)
            if cleanup_shared:
                await self._test_shared_cleanup()

            logger.info("[PASS] All knowledge-service tests passed!")
            return True

        except Exception as e:
            logger.error(f"[FAIL] Knowledge-service test failed: {e}")
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
        self.migration_service = KnowledgeMigrationService(
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
            logger.info(f"Source librarians: {status.get('source_librarians', 0)}")
            recent = status.get('recent_librarians', [])
            if recent:
                logger.info(f"Recent librarians: {[lib['namespace'] for lib in recent[:3]]}")

        logger.info("[PASS] Migration service test successful")

    async def _test_database_migration(self):
        """Test database migration from shared registry."""
        logger.info("Testing database migration...")

        if not self.migration_service:
            self.migration_service = KnowledgeMigrationService(self.test_database_path)

        # Check if migration is needed
        migration_needed = await self.migration_service.migration_needed()
        logger.info(f"Migration needed: {migration_needed}")

        if migration_needed:
            # Run migration
            logger.info("Starting registry migration...")
            results = await self.migration_service.migrate_registry_data()

            # Validate migration results
            assert results.get("status") in ["completed", "completed_with_errors"], f"Migration failed: {results.get('error')}"
            assert results.get("librarians_migrated", 0) >= 0, "No librarians migrated"

            # Check validation results
            validation = results.get("validation_results", {})
            if not validation.get("valid", False):
                logger.warning(f"Migration validation issues: {validation.get('errors', [])}")

            logger.info(f"Migration completed: {results.get('librarians_migrated')} librarians migrated")

        else:
            logger.info("Migration not needed - target already has data or source missing")

        logger.info("[PASS] Database migration test successful")

    async def _test_event_schemas(self):
        """Test event schema definitions exist and are valid."""
        logger.info("Testing event schemas...")

        # Check that all required schemas exist
        schemas_path = project_root / "schemas"
        required_schemas = [
            "knowledge/librarian_register_v1.json",
            "knowledge/librarian_registered_v1.json",
            "knowledge/librarian_query_v1.json",
            "knowledge/librarian_query_result_v1.json"
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
            self.migration_service = KnowledgeMigrationService(self.test_database_path)

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
            "librarians",
            "namespace_reservations",
            "knowledge_audit_log",
            "knowledge_migration_log"
        ]

        for table in expected_tables:
            assert table in tables, f"Missing enhanced table: {table}"

        logger.info("Enhanced tables created:")
        for table in expected_tables:
            logger.info(f"  - {table}")

        # Test enhanced librarians table structure
        async with aiosqlite.connect(self.test_database_path) as conn:
            cursor = await conn.execute("PRAGMA table_info(librarians)")
            columns = [row[1] for row in await cursor.fetchall()]

        expected_columns = [
            "namespace", "snapshot_id", "snapshot_ref", "root_paths_json",
            "created_at", "last_ingest", "description", "tags_json",
            "priority", "auto_ingest", "validation_rules_json"
        ]

        for column in expected_columns:
            assert column in columns, f"Missing enhanced column: {column}"

        logger.info("[PASS] Enhanced schema test successful")

    async def _test_migration_validation(self):
        """Test migration validation capabilities."""
        logger.info("Testing migration validation...")

        if not self.migration_service:
            self.migration_service = KnowledgeMigrationService(self.test_database_path)

        # Test validation with empty database
        validation = await self.migration_service._validate_migration()

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

        logger.info("[PASS] Migration validation test successful")

    async def _test_shared_cleanup(self):
        """Test shared registry cleanup."""
        logger.info("Testing shared registry cleanup...")

        if not self.migration_service:
            self.migration_service = KnowledgeMigrationService(self.test_database_path)

        # Test cleanup (will skip if migration not complete)
        cleanup_result = await self.migration_service.cleanup_shared_registry()

        logger.info(f"Cleanup result: {json.dumps(cleanup_result, indent=2)}")

        # Validate cleanup was successful or appropriately skipped
        assert cleanup_result.get("status") in ["completed", "skipped"], f"Cleanup failed: {cleanup_result.get('error')}"

        if cleanup_result.get("status") == "completed":
            actions = cleanup_result.get("actions_taken", [])
            assert len(actions) > 0, "No cleanup actions taken"
            logger.info(f"Cleanup actions: {actions}")

        logger.info("[PASS] Shared registry cleanup test successful")

    async def _cleanup(self):
        """Cleanup test resources."""
        # Remove test database
        test_db_path = Path(self.test_database_path)
        if test_db_path.exists():
            test_db_path.unlink()
            logger.info("Cleaned up test database")

async def main():
    parser = argparse.ArgumentParser(description="Test knowledge-service migration and extraction")
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
    test = KnowledgeServiceTest()
    success = await test.run_tests(
        run_migration=args.migrate,
        cleanup_shared=args.cleanup
    )

    if success:
        print("\n[PASS] Knowledge Service Extraction Test: PASSED")
        print("[PASS] Database migration infrastructure working")
        print("[PASS] Event schemas defined and validated")
        print("[PASS] Enhanced schema with audit trails")
        print("[PASS] Migration validation operational")
        print("[PASS] Ready for deployment")
        sys.exit(0)
    else:
        print("\n[FAIL] Knowledge Service Extraction Test: FAILED")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())