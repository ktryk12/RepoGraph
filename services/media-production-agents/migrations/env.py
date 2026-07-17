import asyncio
import os
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import AsyncEngine
from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None

def get_database_url():
    return os.getenv('MEDIA-PRODUCTION_AGENTS_DATABASE_URL',
                    "postgresql://localhost/media-production_agents_db")

def run_migrations_offline() -> None:
    url = get_database_url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()

async def run_async_migrations():
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_database_url()
    connectable = AsyncEngine(engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool))
    async with connectable.connect() as connection:
        await connection.run_sync(lambda conn: context.configure(connection=conn, target_metadata=target_metadata) or context.run_migrations())
    await connectable.dispose()

if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
