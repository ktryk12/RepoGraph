"""
Context Plane Bootstrap and Wiring

Implements the bootstrap functionality that was previously in aesa.bootstrap.wiring.
This provides runtime configuration and dependency injection for the context-plane service.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Union
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ContextPlaneRuntime:
    """Runtime configuration for context plane service."""

    store_backend: str
    db_path: str
    artifact_root: str
    redis_url: Optional[str] = None
    max_connections: int = 10
    cache_ttl_seconds: int = 3600
    # Additional attributes expected by service
    context_store: Any = None
    retriever: Any = None
    store: Any = None
    publisher: Any = None
    maintenance: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'store_backend': self.store_backend,
            'db_path': self.db_path,
            'artifact_root': self.artifact_root,
            'redis_url': self.redis_url,
            'max_connections': self.max_connections,
            'cache_ttl_seconds': self.cache_ttl_seconds
        }


def context_plane_store_backend(env: Optional[Mapping[str, str]] = None) -> str:
    """Get context plane store backend from environment."""
    source_env = env or os.environ
    return source_env.get('CONTEXT_PLANE_STORE_BACKEND', 'postgresql')


def context_plane_db_path(env: Optional[Mapping[str, str]] = None) -> str:
    """Get context plane database path from environment."""
    source_env = env or os.environ
    default_path = str(Path.cwd() / "data" / "context_plane.db")
    return source_env.get('CONTEXT_PLANE_DB_PATH', default_path)


def context_plane_artifact_root(env: Optional[Mapping[str, str]] = None) -> str:
    """Get context plane artifact root from environment."""
    source_env = env or os.environ
    default_root = str(Path.cwd() / "artifacts")
    return source_env.get('CONTEXT_PLANE_ARTIFACT_ROOT', default_root)


def context_plane_redis_url(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """Get Redis URL for context plane caching."""
    source_env = env or os.environ
    return source_env.get('CONTEXT_PLANE_REDIS_URL')


def context_plane_max_connections(env: Optional[Mapping[str, str]] = None) -> int:
    """Get maximum database connections."""
    source_env = env or os.environ
    try:
        return int(source_env.get('CONTEXT_PLANE_MAX_CONNECTIONS', '10'))
    except ValueError:
        logger.warning("Invalid CONTEXT_PLANE_MAX_CONNECTIONS, using default: 10")
        return 10


def context_plane_cache_ttl(env: Optional[Mapping[str, str]] = None) -> int:
    """Get cache TTL in seconds."""
    source_env = env or os.environ
    try:
        return int(source_env.get('CONTEXT_PLANE_CACHE_TTL', '3600'))
    except ValueError:
        logger.warning("Invalid CONTEXT_PLANE_CACHE_TTL, using default: 3600")
        return 3600


def build_context_plane_runtime(
    store_backend: Optional[str] = None,
    db_path: Optional[str] = None,
    artifact_root: Optional[str] = None,
    env: Optional[Mapping[str, str]] = None
) -> ContextPlaneRuntime:
    """Build context plane runtime configuration."""

    source_env = env or os.environ

    runtime = ContextPlaneRuntime(
        store_backend=store_backend or context_plane_store_backend(source_env),
        db_path=db_path or context_plane_db_path(source_env),
        artifact_root=artifact_root or context_plane_artifact_root(source_env),
        redis_url=context_plane_redis_url(source_env),
        max_connections=context_plane_max_connections(source_env),
        cache_ttl_seconds=context_plane_cache_ttl(source_env)
    )

    logger.info(f"Built context plane runtime: {runtime.store_backend} backend at {runtime.db_path}")

    # Ensure directories exist
    try:
        os.makedirs(Path(runtime.db_path).parent, exist_ok=True)
        os.makedirs(runtime.artifact_root, exist_ok=True)
        logger.info(f"Created directories for context plane: {runtime.db_path}, {runtime.artifact_root}")
    except OSError as e:
        logger.warning(f"Failed to create directories: {e}")

    return runtime


def validate_context_plane_runtime(runtime: ContextPlaneRuntime) -> bool:
    """Validate context plane runtime configuration."""
    try:
        # Check if database path directory exists or can be created
        db_dir = Path(runtime.db_path).parent
        if not db_dir.exists():
            try:
                db_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                logger.error(f"Cannot create database directory: {db_dir}")
                return False

        # Check if artifact root exists or can be created
        artifact_path = Path(runtime.artifact_root)
        if not artifact_path.exists():
            try:
                artifact_path.mkdir(parents=True, exist_ok=True)
            except OSError:
                logger.error(f"Cannot create artifact directory: {artifact_path}")
                return False

        # Validate store backend
        valid_backends = ['postgresql', 'sqlite', 'memory']
        if runtime.store_backend not in valid_backends:
            logger.error(f"Invalid store backend: {runtime.store_backend}. Must be one of: {valid_backends}")
            return False

        logger.info("Context plane runtime validation successful")
        return True

    except Exception as e:
        logger.error(f"Context plane runtime validation failed: {e}")
        return False


# Legacy function names for backward compatibility
# Note: context_plane_db_path is already defined above - no duplicate needed