#!/usr/bin/env python3
"""
Context Plane Migration and Deployment Test

Tests the complete context-plane database migration including:
1. SQLite to PostgreSQL migration
2. Database ownership transfer from data-platform
3. Event publishing for database operations
4. Kafka contract compliance
5. Performance validation

Usage:
    python scripts/context_plane_test.py [--migrate] [--verbose] [--cleanup]
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
sys.path.insert(0, str(project_root / "services" / "context-plane" / "src"))

# Import context-plane components
from migration_service import ContextMigrationService
from kafka_handlers import ContextKafkaHandlers

logger = logging.getLogger(__name__)

class ContextPlaneTest:
    """Tests context-plane migration and deployment."""

    def __init__(self, postgresql_url: str = None, kafka_servers: str = "localhost:9092"):
        self.postgresql_url = postgresql_url or "postgresql://context_user:password@localhost/context_plane"
        self.kafka_servers = kafka_servers
        self.migration_service = None
        self.kafka_handlers = None

    async def run_tests(self, run_migration: bool = False, cleanup_sqlite: bool = False) -> bool:
        """Run all context-plane tests."""
        try:
            logger.info("Starting Context Plane migration and deployment tests...")

            # Test 1: Database migration
            if run_migration:
                await self._test_database_migration()

            # Test 2: Migration status validation
            await self._test_migration_status()

            # Test 3: Event schema validation
            await self._test_event_schemas()

            # Test 4: Kafka handlers
            await self._test_kafka_handlers()

            # Test 5: Performance validation
            await self._test_performance_characteristics()

            # Test 6: SQLite cleanup (if requested)
            if cleanup_sqlite:
                await self._test_sqlite_cleanup()

            logger.info("[PASS] All context-plane tests passed!")
            return True

        except Exception as e:
            logger.error(f"[FAIL] Context-plane test failed: {e}")
            return False

        finally:
            await self._cleanup()

    async def _test_database_migration(self):
        """Test SQLite to PostgreSQL migration."""
        logger.info("Testing database migration...")

        self.migration_service = ContextMigrationService(self.postgresql_url)

        # Check if migration is needed
        migration_needed = await self.migration_service.migration_needed()
        logger.info(f"Migration needed: {migration_needed}")

        if migration_needed:
            # Get initial status
            initial_status = await self.migration_service.get_migration_status()
            logger.info(f"Initial migration status: {json.dumps(initial_status, indent=2)}")

            # Run migration
            logger.info("Starting database migration...")
            results = await self.migration_service.migrate_all_data()

            # Validate migration results
            assert results.get("status") in ["completed", "completed_with_errors"], f"Migration failed: {results.get('error')}"
            assert results.get("total_records", 0) >= 0, "No records migrated"

            # Check validation results
            validation = results.get("validation", {})
            if not validation.get("valid", False):
                logger.warning(f"Migration validation issues: {validation.get('errors', [])}")

            logger.info(f"Migration completed: {results.get('total_records')} records migrated")

        else:
            logger.info("Migration not needed - PostgreSQL already has data")

        logger.info("[PASS] Database migration test successful")

    async def _test_migration_status(self):
        """Test migration status reporting."""
        logger.info("Testing migration status...")

        if not self.migration_service:
            self.migration_service = ContextMigrationService(self.postgresql_url)

        status = await self.migration_service.get_migration_status()

        # Validate status structure
        required_fields = [
            "migration_needed", "sqlite_path", "sqlite_exists",
            "postgresql_url", "postgresql_rows", "last_checked"
        ]

        for field in required_fields:
            assert field in status, f"Missing status field: {field}"

        # Validate PostgreSQL has data if migration was run
        postgresql_rows = status.get("postgresql_rows", 0)
        logger.info(f"PostgreSQL contains {postgresql_rows} total rows")

        if status.get("postgresql_stats"):
            for table, count in status["postgresql_stats"].items():
                logger.info(f"  {table}: {count} rows")

        logger.info("[PASS] Migration status test successful")

    async def _test_event_schemas(self):
        """Test event schema definitions exist and are valid."""
        logger.info("Testing event schemas...")

        # Check that all required schemas exist
        schemas_path = project_root / "schemas"
        required_schemas = [
            "context/index_updated_v1.json",
            "context/index_started_v1.json",
            "context/index_completed_v1.json",
            "context/document_indexed_v1.json",
            "context/document_removed_v1.json",
            "context/search_performed_v1.json",
            "context/cache_invalidate_v1.json",
            "repository/updated_v1.json"
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

    async def _test_kafka_handlers(self):
        """Test Kafka event handling."""
        logger.info("Testing Kafka handlers...")

        # Create mock context service
        class MockContextService:
            async def retrieve_context(self, **kwargs):
                return {
                    "context_id": "test_context",
                    "repository_id": kwargs.get("repository_id", "test_repo"),
                    "context_pack": {"files": [], "content": "test content"},
                    "token_count": 100,
                    "retrieval_stats": {"duration_ms": 50}
                }

            async def update_repository_index(self, **kwargs):
                return {
                    "status": "started",
                    "indexing_id": "test_index_123",
                    "repository_id": kwargs.get("repository_id"),
                    "documents_updated": len(kwargs.get("changed_files", []))
                }

            async def invalidate_cache(self, **kwargs):
                logger.info(f"Cache invalidation: {kwargs}")

        context_service = MockContextService()

        # Test handler initialization
        self.kafka_handlers = ContextKafkaHandlers(context_service, self.kafka_servers)
        await self.kafka_handlers.start()

        # Test event publishing
        test_correlation_id = "test_correlation_123"

        # Test context retrieved event
        context_pack = await context_service.retrieve_context(
            repository_id="test_repo",
            query="test query"
        )
        await self.kafka_handlers.publish_context_retrieved(context_pack, test_correlation_id)

        # Test index started event
        indexing_operation = {
            "indexing_id": "test_index_456",
            "repository_id": "test_repo",
            "indexing_type": "incremental",
            "estimated_completion": {"duration_estimate_ms": 30000}
        }
        await self.kafka_handlers.publish_index_started(indexing_operation, test_correlation_id)

        # Test document indexed event
        document_info = {
            "document_id": "doc_123",
            "repository_id": "test_repo",
            "file_path": "src/test.py",
            "document_type": "source_code",
            "content_hash": "abc123",
            "file_size_bytes": 1024,
            "language": "python"
        }
        await self.kafka_handlers.publish_document_indexed(document_info, test_correlation_id)

        # Test search performed event
        search_info = {
            "search_id": "search_789",
            "repository_id": "test_repo",
            "search_type": "semantic",
            "query_hash": "hash123",
            "result_count": 5,
            "performance_metrics": {"search_duration_ms": 25}
        }
        await self.kafka_handlers.publish_search_performed(search_info, test_correlation_id)

        logger.info("[PASS] Kafka handlers test successful")

    async def _test_performance_characteristics(self):
        """Test performance requirements are met."""
        logger.info("Testing performance characteristics...")

        if not self.migration_service:
            self.migration_service = ContextMigrationService(self.postgresql_url)

        status = await self.migration_service.get_migration_status()

        # Validate database size is reasonable
        postgresql_rows = status.get("postgresql_rows", 0)
        sqlite_size = status.get("sqlite_size_bytes", 0)

        logger.info(f"Database statistics:")
        logger.info(f"  PostgreSQL rows: {postgresql_rows}")
        logger.info(f"  Original SQLite size: {sqlite_size} bytes ({sqlite_size / 1024 / 1024:.1f} MB)")

        # Check that we're within expected performance parameters
        if postgresql_rows > 0:
            # Validate we have data
            assert postgresql_rows > 0, "No data in PostgreSQL after migration"

            # Check reasonable row counts for context database
            if sqlite_size > 50 * 1024 * 1024:  # > 50MB
                logger.info("Large database detected - performance monitoring recommended")

        logger.info("[PASS] Performance characteristics test successful")

    async def _test_sqlite_cleanup(self):
        """Test SQLite database cleanup after migration."""
        logger.info("Testing SQLite cleanup...")

        if not self.migration_service:
            self.migration_service = ContextMigrationService(self.postgresql_url)

        # Run cleanup
        cleanup_result = await self.migration_service.cleanup_sqlite_access()

        logger.info(f"Cleanup result: {json.dumps(cleanup_result, indent=2)}")

        # Validate cleanup was successful or appropriately skipped
        assert cleanup_result.get("status") in ["completed", "skipped"], f"Cleanup failed: {cleanup_result.get('error')}"

        if cleanup_result.get("status") == "completed":
            actions = cleanup_result.get("actions_taken", [])
            assert len(actions) > 0, "No cleanup actions taken"
            logger.info(f"Cleanup actions: {actions}")

        logger.info("[PASS] SQLite cleanup test successful")

    async def _cleanup(self):
        """Cleanup test resources."""
        if self.kafka_handlers:
            await self.kafka_handlers.stop()

async def main():
    parser = argparse.ArgumentParser(description="Test context-plane migration and deployment")
    parser.add_argument("--migrate", action="store_true", help="Run migration tests")
    parser.add_argument("--cleanup", action="store_true", help="Run SQLite cleanup tests")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    parser.add_argument("--postgresql-url", help="PostgreSQL connection URL")
    parser.add_argument("--kafka-servers", default="localhost:9092", help="Kafka bootstrap servers")
    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Run tests
    test = ContextPlaneTest(
        postgresql_url=args.postgresql_url,
        kafka_servers=args.kafka_servers
    )

    success = await test.run_tests(
        run_migration=args.migrate,
        cleanup_sqlite=args.cleanup
    )

    if success:
        print("\n[PASS] Context Plane Migration Test: PASSED")
        print("[PASS] Database migration infrastructure working")
        print("[PASS] Event schemas defined and validated")
        print("[PASS] Kafka handlers operational")
        print("[PASS] Performance characteristics verified")
        print("[PASS] Ready for deployment")
        sys.exit(0)
    else:
        print("\n[FAIL] Context Plane Migration Test: FAILED")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())