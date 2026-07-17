#!/usr/bin/env python3
"""
Truth Service Deployment Test

Tests the complete truth-service deployment including:
1. Database initialization and schema creation
2. Migration from shared databases
3. Service functionality (facts and proposals)
4. Event publishing
5. API endpoints

Usage:
    python scripts/truth_service_test.py [--migrate] [--verbose]
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
sys.path.insert(0, str(project_root / "services" / "truth-service" / "src"))

# Import truth service components
from truth_database import TruthDatabase
from truth_service import TruthService
from migration_service import TruthMigrationService
from kafka_handlers import TruthKafkaHandlers

logger = logging.getLogger(__name__)

class TruthServiceTest:
    """Tests truth service deployment."""

    def __init__(self, test_database_path: str = "test_truth.db"):
        self.test_database_path = test_database_path
        self.database = None
        self.service = None
        self.migration_service = None
        self.kafka_handlers = None

    async def run_tests(self, run_migration: bool = False) -> bool:
        """Run all truth service tests."""
        try:
            logger.info("Starting Truth Service deployment tests...")

            # Test 1: Database initialization
            await self._test_database_initialization()

            # Test 2: Migration (if requested)
            if run_migration:
                await self._test_migration()

            # Test 3: Service functionality
            await self._test_service_functionality()

            # Test 4: Event handling
            await self._test_event_handling()

            # Test 5: API endpoints (simulated)
            await self._test_api_endpoints()

            logger.info("✅ All truth service tests passed!")
            return True

        except Exception as e:
            logger.error(f"❌ Truth service test failed: {e}")
            return False

        finally:
            await self._cleanup()

    async def _test_database_initialization(self):
        """Test database initialization and schema creation."""
        logger.info("Testing database initialization...")

        # Remove test database if exists
        test_db_path = Path(self.test_database_path)
        if test_db_path.exists():
            test_db_path.unlink()

        # Initialize database
        self.database = TruthDatabase(self.test_database_path)
        await self.database.initialize()

        # Check database health
        is_healthy = await self.database.health_check()
        assert is_healthy, "Database health check failed"

        # Verify schema was created
        async with self.database.get_connection() as conn:
            cursor = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            tables = [row[0] for row in await cursor.fetchall()]

        expected_tables = [
            "truth_facts", "truth_proposals", "truth_relationships",
            "truth_versions", "truth_query_cache", "truth_migration_log"
        ]

        for table in expected_tables:
            assert table in tables, f"Missing table: {table}"

        logger.info("✅ Database initialization successful")

    async def _test_migration(self):
        """Test migration from shared databases."""
        logger.info("Testing migration service...")

        self.migration_service = TruthMigrationService(self.database)

        # Check if migration is needed
        migration_needed = await self.migration_service.migration_needed()
        logger.info(f"Migration needed: {migration_needed}")

        if migration_needed:
            # Run migration
            results = await self.migration_service.migrate_all_sources()
            logger.info(f"Migration results: {json.dumps(results, indent=2)}")

        # Check migration status
        status = await self.migration_service.get_migration_status()
        logger.info(f"Migration status: {json.dumps(status, indent=2)}")

        logger.info("✅ Migration service test successful")

    async def _test_service_functionality(self):
        """Test core service functionality."""
        logger.info("Testing service functionality...")

        self.service = TruthService(self.database)

        # Test fact creation
        fact_data = {
            "fact_content": "The sky is blue",
            "fact_type": "observation",
            "confidence": 0.95,
            "created_by": "test_user",
            "source_id": "test_source",
            "tags": ["color", "sky", "observation"],
            "metadata": {"test": True}
        }

        fact_id = await self.service.create_fact(fact_data)
        assert fact_id, "Failed to create fact"
        logger.info(f"Created fact: {fact_id}")

        # Test fact retrieval
        retrieved_fact = await self.service.get_fact(fact_id)
        assert retrieved_fact, "Failed to retrieve fact"
        assert retrieved_fact["fact_content"] == fact_data["fact_content"]
        logger.info("✅ Fact creation and retrieval successful")

        # Test proposal creation
        proposal_data = {
            "proposed_fact": "The sky is sometimes gray",
            "proposal_type": "new_fact",
            "justification": "Observed during cloudy weather",
            "submitted_by": "test_user",
            "evidence_data": {
                "sources": [
                    {
                        "source_type": "observation",
                        "content": "Visual observation during storm",
                        "reliability": 0.8
                    }
                ]
            }
        }

        proposal_id = await self.service.create_proposal(proposal_data)
        assert proposal_id, "Failed to create proposal"
        logger.info(f"Created proposal: {proposal_id}")

        # Test proposal review
        review_result = await self.service.review_proposal(
            proposal_id, "approved", "test_reviewer", "Looks good"
        )
        assert review_result["decision"] == "approved"
        logger.info("✅ Proposal creation and review successful")

        # Test relationship creation
        fact2_data = {
            "fact_content": "Weather affects sky color",
            "fact_type": "rule",
            "created_by": "test_user"
        }
        fact2_id = await self.service.create_fact(fact2_data)

        relationship_id = await self.service.create_fact_relationship(
            fact_id, fact2_id, "supports", 0.8, "Sky color depends on weather"
        )
        assert relationship_id, "Failed to create relationship"
        logger.info("✅ Relationship creation successful")

        # Test search
        search_results = await self.service.search_facts("sky")
        assert len(search_results) >= 1, "Search returned no results"
        logger.info("✅ Fact search successful")

        logger.info("✅ Service functionality test successful")

    async def _test_event_handling(self):
        """Test Kafka event handling."""
        logger.info("Testing event handling...")

        self.kafka_handlers = TruthKafkaHandlers(self.service, "localhost:9092")
        await self.kafka_handlers.start()

        # Test event publishing (simulated)
        fact = {
            "fact_id": "test_fact_id",
            "fact_content": "Test fact content",
            "fact_type": "assertion",
            "confidence": 1.0,
            "source_id": "test",
            "source_type": "test",
            "status": "active",
            "version": 1,
            "created_at": "2026-04-27T12:00:00",
            "created_by": "test",
            "tags": [],
            "metadata": {}
        }

        # Test fact created event
        await self.kafka_handlers.publish_fact_created(fact)

        # Test proposal received event
        proposal = {
            "proposal_id": "test_proposal_id",
            "proposed_fact": "Test proposal content",
            "proposal_type": "new_fact",
            "submitted_by": "test_user",
            "submitted_at": "2026-04-27T12:00:00",
            "priority": "normal"
        }

        await self.kafka_handlers.publish_proposal_received(proposal)

        await self.kafka_handlers.stop()

        logger.info("✅ Event handling test successful")

    async def _test_api_endpoints(self):
        """Test API endpoints (simulated)."""
        logger.info("Testing API endpoints...")

        # Test metrics endpoint
        metrics = await self.service.get_metrics()
        assert "truth_service" in metrics
        assert "timestamp" in metrics
        logger.info(f"Metrics: {json.dumps(metrics, indent=2)}")

        # Test fact listing
        facts = await self.service.list_facts(limit=10)
        assert isinstance(facts, list)
        logger.info(f"Listed {len(facts)} facts")

        # Test proposal listing
        proposals = await self.service.list_proposals(limit=10)
        assert isinstance(proposals, list)
        logger.info(f"Listed {len(proposals)} proposals")

        logger.info("✅ API endpoints test successful")

    async def _cleanup(self):
        """Cleanup test resources."""
        if self.database:
            await self.database.close()

        # Remove test database
        test_db_path = Path(self.test_database_path)
        if test_db_path.exists():
            test_db_path.unlink()
            logger.info("Cleaned up test database")

async def main():
    parser = argparse.ArgumentParser(description="Test truth service deployment")
    parser.add_argument("--migrate", action="store_true", help="Run migration tests")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")
    args = parser.parse_args()

    # Configure logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    # Run tests
    test = TruthServiceTest()
    success = await test.run_tests(run_migration=args.migrate)

    if success:
        print("\n✅ Truth Service Deployment Test: PASSED")
        print("✅ Database initialization working")
        print("✅ Service functionality verified")
        print("✅ Event handling operational")
        print("✅ Ready for deployment")
        sys.exit(0)
    else:
        print("\n❌ Truth Service Deployment Test: FAILED")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
