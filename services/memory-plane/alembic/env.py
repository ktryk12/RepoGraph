"""Alembic environment for memory-plane database migrations"""

from logging.config import fileConfig
import os
import sys
from pathlib import Path

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# Add the src directory to Python path to import models
src_dir = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_dir))

# Import the SQLAlchemy metadata
from memory_plane_models import metadata

# This is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Add your model's MetaData object here
# for 'autogenerate' support
target_metadata = metadata

# Other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.

def get_database_url():
    """Get database URL from environment variables"""
    # In production, use DATABASE_URL_MEMORY_PLANE
    db_url = os.getenv("DATABASE_URL_MEMORY_PLANE")
    if db_url:
        return db_url

    # For development, construct from components
    user = "babyai_memory_plane_user"
    password = os.getenv("POSTGRES_PASSWORD", "memory_plane_secure_password_change_in_prod")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_DECISION_PORT", "5432")
    database = "babyai_memory_plane"

    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    # Override the sqlalchemy.url in alembic.ini with our dynamic URL
    config_dict = config.get_section(config.config_ini_section, {})
    config_dict["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        config_dict,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()