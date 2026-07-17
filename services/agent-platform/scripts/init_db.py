#!/usr/bin/env python3
"""
Database Initialization Script for Agent Platform Service

Creates the agent platform database and user with proper permissions
following the database-per-service pattern.
"""

import os
import sys
import asyncio
import asyncpg
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Database configuration
DB_NAME = "babyai_agent_platform"
DB_USER = "agent_platform_user"
DB_PASSWORD = "agent_platform_pass"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "admin"  # Should be from environment in production

async def create_database_and_user():
    """Create agent platform database and user"""
    try:
        # Connect as admin to create database and user
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            user=ADMIN_USER,
            password=ADMIN_PASSWORD,
            database="postgres"  # Connect to default database
        )

        # Create user if not exists
        try:
            await conn.execute(f"""
                CREATE USER {DB_USER} WITH PASSWORD '{DB_PASSWORD}';
            """)
            logger.info(f"Created user: {DB_USER}")
        except asyncpg.DuplicateObjectError:
            logger.info(f"User {DB_USER} already exists")

        # Create database if not exists
        try:
            await conn.execute(f"""
                CREATE DATABASE {DB_NAME} OWNER {DB_USER};
            """)
            logger.info(f"Created database: {DB_NAME}")
        except asyncpg.DuplicateObjectError:
            logger.info(f"Database {DB_NAME} already exists")

        await conn.close()

        # Connect to new database and grant permissions
        conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            user=ADMIN_USER,
            password=ADMIN_PASSWORD,
            database=DB_NAME
        )

        # Grant all privileges to the service user
        await conn.execute(f"""
            GRANT ALL PRIVILEGES ON DATABASE {DB_NAME} TO {DB_USER};
        """)

        # Grant schema permissions
        await conn.execute(f"""
            GRANT ALL PRIVILEGES ON SCHEMA public TO {DB_USER};
        """)

        # Grant table creation permissions
        await conn.execute(f"""
            GRANT CREATE ON SCHEMA public TO {DB_USER};
        """)

        await conn.close()

        logger.info(f"Database {DB_NAME} initialized successfully for {DB_USER}")

        # Test connection as service user
        test_conn = await asyncpg.connect(
            host="localhost",
            port=5432,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME
        )
        await test_conn.close()
        logger.info("Service user connection test successful")

        return True

    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        return False

async def test_store_connection():
    """Test PostgreSQL store connection"""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from postgresql_agent_store import PostgreSQLAgentStore

        database_url = f"postgresql://{DB_USER}:{DB_PASSWORD}@localhost:5432/{DB_NAME}"

        # Create store and test connection
        store = await PostgreSQLAgentStore.create(database_url)

        # Test basic operations
        await store.create_agent_definition(
            agent_id="test_agent",
            agent_name="Test Agent",
            agent_type="test",
            agent_spec={"version": "1.0"},
            metadata={"test": True}
        )

        agent = await store.get_agent_definition("test_agent")
        assert agent is not None
        assert agent["agent_name"] == "Test Agent"

        await store.close()
        logger.info("PostgreSQL store test successful")
        return True

    except Exception as e:
        logger.error(f"Store connection test failed: {e}")
        return False

async def main():
    """Main initialization function"""
    logger.info("Starting Agent Platform database initialization...")

    # Create database and user
    if not await create_database_and_user():
        sys.exit(1)

    # Test store connection
    if not await test_store_connection():
        sys.exit(1)

    logger.info("Agent Platform database initialization complete!")

    print("\nNext steps:")
    print("1. Run migrations: cd services/agent-platform/migrations && alembic upgrade head")
    print(f"2. Set AGENT_DATABASE_URL=postgresql://{DB_USER}:{DB_PASSWORD}@localhost:5432/{DB_NAME}")
    print("3. Start the agent platform service")

if __name__ == "__main__":
    asyncio.run(main())