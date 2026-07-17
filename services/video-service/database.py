"""
Video Service Database Configuration

Dedicated database connection and management for video-service.
Follows database-per-service pattern - no shared database dependencies.
"""

import os
import logging
from contextlib import contextmanager
from typing import Optional, Generator

from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool

from models import Base, SCHEMA_VERSION

logger = logging.getLogger(__name__)


class VideoServiceDatabase:
    """
    Database manager for video-service

    Manages dedicated database connection for video processing operations.
    Isolated from other microservices following database-per-service pattern.
    """

    def __init__(self):
        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None
        self._database_url = self._get_database_url()

    def _get_database_url(self) -> str:
        """Get database URL from environment variables"""
        # Video service specific database configuration
        host = os.getenv("VIDEO_DB_HOST", "localhost")
        port = os.getenv("VIDEO_DB_PORT", "5432")
        database = os.getenv("VIDEO_DB_NAME", "video_service_db")
        username = os.getenv("VIDEO_DB_USER", "video_service_user")
        password = os.getenv("VIDEO_DB_PASSWORD", "video_service_password")

        return f"postgresql://{username}:{password}@{host}:{port}/{database}"

    def initialize(self) -> None:
        """Initialize database connection and session factory"""
        try:
            self._engine = create_engine(
                self._database_url,
                poolclass=QueuePool,
                pool_size=5,
                max_overflow=10,
                pool_timeout=30,
                pool_recycle=3600,
                echo=os.getenv("VIDEO_DB_ECHO", "false").lower() == "true"
            )

            # Test connection
            with self._engine.connect() as conn:
                conn.execute("SELECT 1")
                logger.info("Video service database connection established")

            self._session_factory = sessionmaker(bind=self._engine)

        except Exception as e:
            logger.error(f"Failed to initialize video service database: {e}")
            raise

    def create_tables(self) -> None:
        """Create all database tables"""
        if not self._engine:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        try:
            Base.metadata.create_all(self._engine)
            logger.info("Video service database tables created successfully")
        except Exception as e:
            logger.error(f"Failed to create video service database tables: {e}")
            raise

    def drop_tables(self) -> None:
        """Drop all database tables (use with caution!)"""
        if not self._engine:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        try:
            Base.metadata.drop_all(self._engine)
            logger.warning("Video service database tables dropped")
        except Exception as e:
            logger.error(f"Failed to drop video service database tables: {e}")
            raise

    @contextmanager
    def get_session(self) -> Generator[Session, None, None]:
        """Get database session with automatic cleanup"""
        if not self._session_factory:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            session.close()

    def health_check(self) -> dict:
        """Perform database health check"""
        try:
            if not self._engine:
                return {"status": "unhealthy", "error": "Database not initialized"}

            with self._engine.connect() as conn:
                result = conn.execute("SELECT 1")
                row = result.fetchone()

                if row and row[0] == 1:
                    return {
                        "status": "healthy",
                        "database": "video_service_db",
                        "schema_version": SCHEMA_VERSION,
                        "connection_pool": {
                            "size": self._engine.pool.size(),
                            "checked_in": self._engine.pool.checkedin(),
                            "checked_out": self._engine.pool.checkedout()
                        }
                    }
                else:
                    return {"status": "unhealthy", "error": "Invalid health check response"}

        except Exception as e:
            logger.error(f"Video service database health check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}

    def get_stats(self) -> dict:
        """Get database statistics"""
        try:
            with self.get_session() as session:
                # Import here to avoid circular imports
                from models import VideoJob, VideoScript, VideoAsset, VideoProvider

                stats = {
                    "video_jobs": {
                        "total": session.query(VideoJob).count(),
                        "active": session.query(VideoJob).filter(
                            VideoJob.status.in_(['created', 'processing'])
                        ).count(),
                        "completed": session.query(VideoJob).filter(
                            VideoJob.status == 'completed'
                        ).count(),
                        "failed": session.query(VideoJob).filter(
                            VideoJob.status == 'failed'
                        ).count()
                    },
                    "video_scripts": {
                        "total": session.query(VideoScript).count()
                    },
                    "video_assets": {
                        "total": session.query(VideoAsset).count(),
                        "by_type": {}
                    },
                    "video_providers": {
                        "total": session.query(VideoProvider).count(),
                        "enabled": session.query(VideoProvider).filter(
                            VideoProvider.is_enabled == True
                        ).count()
                    }
                }

                return stats

        except Exception as e:
            logger.error(f"Failed to get video service database stats: {e}")
            return {"error": str(e)}

    def close(self) -> None:
        """Close database connections"""
        if self._engine:
            self._engine.dispose()
            logger.info("Video service database connections closed")


# Global database instance
video_db = VideoServiceDatabase()


# Convenience functions
def init_database() -> None:
    """Initialize video service database"""
    video_db.initialize()


def create_database_tables() -> None:
    """Create video service database tables"""
    video_db.create_tables()


def get_database_session() -> Generator[Session, None, None]:
    """Get video service database session"""
    return video_db.get_session()


def database_health_check() -> dict:
    """Get video service database health status"""
    return video_db.health_check()


if __name__ == "__main__":
    # Basic database setup for testing
    logging.basicConfig(level=logging.INFO)

    print("Initializing video service database...")
    init_database()

    print("Creating tables...")
    create_database_tables()

    print("Testing connection...")
    health = database_health_check()
    print(f"Health check: {health}")

    print("Video service database setup complete!")