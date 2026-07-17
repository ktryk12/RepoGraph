"""
Initialize PostgreSQL database for orchestrator-worker service

Creates the database, user, and runs initial migrations.
Follows the database-per-service pattern.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
import asyncpg

logger = logging.getLogger(__name__)

# Database configuration
DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", "5432")),
    "admin_user": os.getenv("POSTGRES_ADMIN_USER", "admin"),
    "admin_password": os.getenv("POSTGRES_ADMIN_PASSWORD", "admin"),
    "database_name": "babyai_orchestrator_worker",
    "service_user": "orchestrator_worker_user",
    "service_password": "orchestrator_worker_pass",
}


async def create_database_and_user():
    """Create database and service user if they don't exist"""
    admin_conn = await asyncpg.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["admin_user"],
        password=DB_CONFIG["admin_password"],
        database="postgres",
    )

    try:
        # Check if database exists
        db_exists = await admin_conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1",
            DB_CONFIG["database_name"]
        )

        if not db_exists:
            await admin_conn.execute(f'CREATE DATABASE "{DB_CONFIG["database_name"]}"')
            print(f"✓ Created database: {DB_CONFIG['database_name']}")
        else:
            print(f"✓ Database already exists: {DB_CONFIG['database_name']}")

        # Check if user exists
        user_exists = await admin_conn.fetchval(
            "SELECT 1 FROM pg_user WHERE usename = $1",
            DB_CONFIG["service_user"]
        )

        if not user_exists:
            await admin_conn.execute(
                f'CREATE USER "{DB_CONFIG["service_user"]}" WITH PASSWORD \'{DB_CONFIG["service_password"]}\''
            )
            print(f"✓ Created user: {DB_CONFIG['service_user']}")
        else:
            print(f"✓ User already exists: {DB_CONFIG['service_user']}")

        # Grant permissions
        await admin_conn.execute(f'GRANT ALL PRIVILEGES ON DATABASE "{DB_CONFIG["database_name"]}" TO "{DB_CONFIG["service_user"]}"')
        print(f"✓ Granted database privileges to: {DB_CONFIG['service_user']}")

    finally:
        await admin_conn.close()


async def setup_schema_permissions():
    """Set up schema permissions for the service user"""
    admin_conn = await asyncpg.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["admin_user"],
        password=DB_CONFIG["admin_password"],
        database=DB_CONFIG["database_name"],
    )

    try:
        # Grant schema permissions
        await admin_conn.execute(f'GRANT CREATE ON SCHEMA public TO "{DB_CONFIG["service_user"]}"')
        await admin_conn.execute(f'GRANT USAGE ON SCHEMA public TO "{DB_CONFIG["service_user"]}"')
        print(f"✓ Granted schema permissions to: {DB_CONFIG['service_user']}")

    finally:
        await admin_conn.close()


async def run_initial_migration():
    """Run the initial database migration"""
    print("Running initial database migration...")

    service_conn = await asyncpg.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["service_user"],
        password=DB_CONFIG["service_password"],
        database=DB_CONFIG["database_name"],
    )

    try:
        # Episodes table
        await service_conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                episode_id VARCHAR(100) PRIMARY KEY,
                workflow_id VARCHAR(100) NOT NULL,
                status VARCHAR(20) NOT NULL,
                task_ref TEXT NOT NULL,
                truth_pack_ref TEXT NOT NULL,
                context_id VARCHAR(100) NOT NULL,
                metadata_json JSON,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL,
                execution_result JSON,
                final_score FLOAT,
                error_message TEXT
            )
        """)

        # Workflow states table
        await service_conn.execute("""
            CREATE TABLE IF NOT EXISTS workflow_states (
                workflow_id VARCHAR(100) PRIMARY KEY,
                episode_id VARCHAR(100) NOT NULL,
                current_node VARCHAR(100),
                completed_nodes JSON,
                state_data JSON,
                status VARCHAR(20) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
        """)

        # Worker results table
        await service_conn.execute("""
            CREATE TABLE IF NOT EXISTS worker_results (
                result_id VARCHAR(100) PRIMARY KEY,
                workflow_id VARCHAR(100) NOT NULL,
                episode_id VARCHAR(100) NOT NULL,
                worker_type VARCHAR(50) NOT NULL,
                partition_id VARCHAR(100) NOT NULL,
                result_data JSON,
                execution_time_ms INTEGER NOT NULL,
                status VARCHAR(20) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                error_message TEXT
            )
        """)

        # Create indexes
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_episodes_status ON episodes(status)",
            "CREATE INDEX IF NOT EXISTS idx_episodes_created_at ON episodes(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_episodes_workflow_id ON episodes(workflow_id)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_states_status ON workflow_states(status)",
            "CREATE INDEX IF NOT EXISTS idx_workflow_states_episode_id ON workflow_states(episode_id)",
            "CREATE INDEX IF NOT EXISTS idx_worker_results_workflow_id ON worker_results(workflow_id)",
            "CREATE INDEX IF NOT EXISTS idx_worker_results_worker_type ON worker_results(worker_type)",
            "CREATE INDEX IF NOT EXISTS idx_worker_results_status ON worker_results(status)",
        ]

        for index_sql in indexes:
            await service_conn.execute(index_sql)

        print("✓ Database schema created successfully")

    finally:
        await service_conn.close()


async def test_database_connection():
    """Test the database connection"""
    print("Testing database connection...")

    service_conn = await asyncpg.connect(
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
        user=DB_CONFIG["service_user"],
        password=DB_CONFIG["service_password"],
        database=DB_CONFIG["database_name"],
    )

    try:
        result = await service_conn.fetchval("SELECT 1")
        assert result == 1
        print("✓ Database connection test passed")

    finally:
        await service_conn.close()


async def main():
    """Initialize the orchestrator-worker database"""
    print("=== Initializing Orchestrator-Worker Database ===")

    try:
        await create_database_and_user()
        await setup_schema_permissions()
        await run_initial_migration()
        await test_database_connection()

        print("\n🎉 Orchestrator-Worker database initialization complete!")
        print(f"Database: {DB_CONFIG['database_name']}")
        print(f"User: {DB_CONFIG['service_user']}")
        print(f"Connection: postgresql://{DB_CONFIG['service_user']}:***@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database_name']}")

    except Exception as e:
        print(f"\n❌ Database initialization failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())