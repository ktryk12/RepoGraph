#!/usr/bin/env python3
"""
Video Service Database Migration Script

This script initializes the video-service database and creates all required tables.
Run this script to set up the database schema for the first time or after schema changes.
"""

import logging
import sys
from pathlib import Path

# Add the service directory to Python path for imports
sys.path.insert(0, str(Path(__file__).parent))

from database import init_database, create_database_tables, database_health_check
from models import SCHEMA_VERSION


def main():
    """Run database migration for video-service"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    logger = logging.getLogger("video-service-migration")

    try:
        logger.info(f"Starting video-service database migration (schema version: {SCHEMA_VERSION})")

        # Initialize database connection
        logger.info("Initializing database connection...")
        init_database()

        # Create tables
        logger.info("Creating database tables...")
        create_database_tables()

        # Verify health
        logger.info("Verifying database health...")
        health = database_health_check()

        if health.get("status") == "healthy":
            logger.info("✅ Migration completed successfully!")
            logger.info(f"Database: {health.get('database')}")
            logger.info(f"Schema version: {health.get('schema_version')}")
            logger.info(f"Connection pool status: {health.get('connection_pool')}")
            return 0
        else:
            logger.error(f"❌ Database health check failed: {health}")
            return 1

    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        logger.exception("Full error details:")
        return 1


if __name__ == "__main__":
    sys.exit(main())