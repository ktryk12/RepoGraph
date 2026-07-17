"""
Truth Service - Main Entry Point

ADR-0015 Phase 2: Database-per-Service
Consolidates truth management from shared databases into dedicated service.

Responsibilities:
- Truth fact management and versioning
- Proposal lifecycle management
- Event-driven truth updates
- Migration from shared databases
"""

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from truth_database import TruthDatabase
from truth_service import TruthService
from kafka_handlers import TruthKafkaHandlers
from migration_service import TruthMigrationService

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("truth-service")

class TruthServiceApp:
    """Main truth service application."""

    def __init__(self):
        self.app = FastAPI(
            title="Truth Service",
            description="Truth management and proposal lifecycle service",
            version="1.0.0"
        )

        # Core components
        self.database: Optional[TruthDatabase] = None
        self.service: Optional[TruthService] = None
        self.kafka_handlers: Optional[TruthKafkaHandlers] = None
        self.migration_service: Optional[TruthMigrationService] = None

        # Shutdown handling
        self.shutdown_event = asyncio.Event()

        # Setup routes
        self._setup_routes()

    def _setup_routes(self):
        """Setup FastAPI routes."""

        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            if not self.database:
                return {"status": "initializing"}

            try:
                # Check database connectivity
                is_healthy = await self.database.health_check()

                # Check migration status
                migration_status = None
                if self.migration_service:
                    migration_status = await self.migration_service.get_migration_status()

                return {
                    "status": "healthy" if is_healthy else "unhealthy",
                    "database": "connected" if is_healthy else "disconnected",
                    "migration": migration_status,
                    "service": "truth-service",
                    "version": "1.0.0"
                }
            except Exception as e:
                logger.error(f"Health check failed: {e}")
                return {"status": "unhealthy", "error": str(e)}

        @self.app.get("/metrics")
        async def metrics():
            """Prometheus metrics endpoint."""
            if not self.service:
                return {"error": "service not initialized"}

            try:
                return await self.service.get_metrics()
            except Exception as e:
                logger.error(f"Failed to get metrics: {e}")
                return {"error": str(e)}

        # Migration compatibility endpoints (Phase 2 only)
        @self.app.get("/api/v1/facts")
        async def list_facts(limit: int = 100, offset: int = 0):
            """List facts - migration compatibility endpoint."""
            if not self.service:
                return {"error": "service not initialized"}

            try:
                facts = await self.service.list_facts(limit=limit, offset=offset)
                return {"facts": facts}
            except Exception as e:
                logger.error(f"Failed to list facts: {e}")
                return {"error": str(e)}

        @self.app.post("/api/v1/facts")
        async def create_fact(fact_data: dict):
            """Create fact - migration compatibility endpoint."""
            if not self.service:
                return {"error": "service not initialized"}

            try:
                fact_id = await self.service.create_fact(fact_data)
                return {"fact_id": fact_id}
            except Exception as e:
                logger.error(f"Failed to create fact: {e}")
                return {"error": str(e)}

        @self.app.get("/api/v1/proposals")
        async def list_proposals(status: str = "pending", limit: int = 100):
            """List proposals - migration compatibility endpoint."""
            if not self.service:
                return {"error": "service not initialized"}

            try:
                proposals = await self.service.list_proposals(status=status, limit=limit)
                return {"proposals": proposals}
            except Exception as e:
                logger.error(f"Failed to list proposals: {e}")
                return {"error": str(e)}

        @self.app.post("/api/v1/proposals")
        async def create_proposal(proposal_data: dict):
            """Create proposal - migration compatibility endpoint."""
            if not self.service:
                return {"error": "service not initialized"}

            try:
                proposal_id = await self.service.create_proposal(proposal_data)
                return {"proposal_id": proposal_id}
            except Exception as e:
                logger.error(f"Failed to create proposal: {e}")
                return {"error": str(e)}

    async def initialize(self):
        """Initialize all service components."""
        logger.info("Initializing Truth Service...")

        try:
            # Initialize database
            database_path = os.getenv("DATABASE_PATH", "database/truth.db")
            self.database = TruthDatabase(database_path)
            await self.database.initialize()

            # Initialize core service
            self.service = TruthService(self.database)

            # Initialize migration service
            self.migration_service = TruthMigrationService(self.database)

            # Check if migration is needed
            if await self.migration_service.migration_needed():
                logger.info("Starting database migration from shared sources...")
                await self.migration_service.migrate_all_sources()
                logger.info("Database migration completed")

            # Initialize Kafka handlers
            kafka_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
            self.kafka_handlers = TruthKafkaHandlers(self.service, kafka_servers)
            await self.kafka_handlers.start()

            logger.info("Truth Service initialization complete")

        except Exception as e:
            logger.error(f"Failed to initialize truth service: {e}")
            raise

    async def shutdown(self):
        """Graceful shutdown."""
        logger.info("Shutting down Truth Service...")

        try:
            # Stop Kafka handlers
            if self.kafka_handlers:
                await self.kafka_handlers.stop()

            # Close database
            if self.database:
                await self.database.close()

            logger.info("Truth Service shutdown complete")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}, initiating shutdown...")
            self.shutdown_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

# Global app instance
app_instance = TruthServiceApp()
app = app_instance.app

async def startup():
    """FastAPI startup event."""
    await app_instance.initialize()

async def shutdown():
    """FastAPI shutdown event."""
    await app_instance.shutdown()

# Register startup/shutdown events
app.add_event_handler("startup", startup)
app.add_event_handler("shutdown", shutdown)

def main():
    """Main entry point for standalone execution."""
    import uvicorn

    # Setup signal handlers
    app_instance.setup_signal_handlers()

    # Run server
    port = int(os.getenv("PORT", "8080"))
    host = os.getenv("HOST", "0.0.0.0")

    logger.info(f"Starting Truth Service on {host}:{port}")

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
        access_log=True
    )

if __name__ == "__main__":
    main()