"""
Voice Service Database Configuration

Dedicated database connection and management for voice-service.
Handles STT/TTS data and MCP server sessions following database-per-service pattern.
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


class VoiceServiceDatabase:
    """
    Database manager for voice-service

    Manages dedicated database connection for voice processing operations.
    Isolated from other microservices following database-per-service pattern.
    """

    def __init__(self):
        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None
        self._database_url = self._get_database_url()

    def _get_database_url(self) -> str:
        """Get database URL from environment variables"""
        # Voice service specific database configuration
        host = os.getenv("VOICE_DB_HOST", "localhost")
        port = os.getenv("VOICE_DB_PORT", "5432")
        database = os.getenv("VOICE_DB_NAME", "voice_service_db")
        username = os.getenv("VOICE_DB_USER", "voice_service_user")
        password = os.getenv("VOICE_DB_PASSWORD", "voice_service_password")

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
                echo=os.getenv("VOICE_DB_ECHO", "false").lower() == "true"
            )

            # Test connection
            with self._engine.connect() as conn:
                conn.execute("SELECT 1")
                logger.info("Voice service database connection established")

            self._session_factory = sessionmaker(bind=self._engine)

        except Exception as e:
            logger.error(f"Failed to initialize voice service database: {e}")
            raise

    def create_tables(self) -> None:
        """Create all database tables"""
        if not self._engine:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        try:
            Base.metadata.create_all(self._engine)
            logger.info("Voice service database tables created successfully")
        except Exception as e:
            logger.error(f"Failed to create voice service database tables: {e}")
            raise

    def drop_tables(self) -> None:
        """Drop all database tables (use with caution!)"""
        if not self._engine:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        try:
            Base.metadata.drop_all(self._engine)
            logger.warning("Voice service database tables dropped")
        except Exception as e:
            logger.error(f"Failed to drop voice service database tables: {e}")
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
                        "database": "voice_service_db",
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
            logger.error(f"Voice service database health check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}

    def get_stats(self) -> dict:
        """Get database statistics"""
        try:
            with self.get_session() as session:
                # Import here to avoid circular imports
                from models import VoiceJob, VoiceModel, AudioAsset, MCPSession, VoiceCache

                stats = {
                    "voice_jobs": {
                        "total": session.query(VoiceJob).count(),
                        "active": session.query(VoiceJob).filter(
                            VoiceJob.status.in_(['created', 'processing'])
                        ).count(),
                        "completed": session.query(VoiceJob).filter(
                            VoiceJob.status == 'completed'
                        ).count(),
                        "failed": session.query(VoiceJob).filter(
                            VoiceJob.status == 'failed'
                        ).count(),
                        "by_type": {}
                    },
                    "voice_models": {
                        "total": session.query(VoiceModel).count(),
                        "enabled": session.query(VoiceModel).filter(
                            VoiceModel.is_enabled == True
                        ).count(),
                        "loaded": session.query(VoiceModel).filter(
                            VoiceModel.is_loaded == True
                        ).count()
                    },
                    "audio_assets": {
                        "total": session.query(AudioAsset).count(),
                        "by_type": {}
                    },
                    "mcp_sessions": {
                        "total": session.query(MCPSession).count(),
                        "active": session.query(MCPSession).filter(
                            MCPSession.status == 'active'
                        ).count()
                    },
                    "voice_cache": {
                        "total": session.query(VoiceCache).count(),
                        "cache_hit_potential": session.query(VoiceCache).filter(
                            VoiceCache.access_count > 0
                        ).count()
                    }
                }

                return stats

        except Exception as e:
            logger.error(f"Failed to get voice service database stats: {e}")
            return {"error": str(e)}

    def cleanup_expired_cache(self) -> int:
        """Clean up expired cache entries"""
        try:
            with self.get_session() as session:
                from models import VoiceCache
                from datetime import datetime

                expired_count = session.query(VoiceCache).filter(
                    VoiceCache.expires_at < datetime.utcnow()
                ).count()

                if expired_count > 0:
                    session.query(VoiceCache).filter(
                        VoiceCache.expires_at < datetime.utcnow()
                    ).delete()
                    session.commit()
                    logger.info(f"Cleaned up {expired_count} expired cache entries")

                return expired_count

        except Exception as e:
            logger.error(f"Failed to cleanup expired cache: {e}")
            return 0

    def cleanup_inactive_sessions(self, hours_inactive: int = 24) -> int:
        """Clean up inactive MCP sessions"""
        try:
            with self.get_session() as session:
                from models import MCPSession
                from datetime import datetime, timedelta

                cutoff_time = datetime.utcnow() - timedelta(hours=hours_inactive)
                inactive_count = session.query(MCPSession).filter(
                    MCPSession.last_activity_at < cutoff_time,
                    MCPSession.status != 'terminated'
                ).count()

                if inactive_count > 0:
                    session.query(MCPSession).filter(
                        MCPSession.last_activity_at < cutoff_time,
                        MCPSession.status != 'terminated'
                    ).update({"status": "terminated"})
                    session.commit()
                    logger.info(f"Marked {inactive_count} inactive sessions as terminated")

                return inactive_count

        except Exception as e:
            logger.error(f"Failed to cleanup inactive sessions: {e}")
            return 0

    def close(self) -> None:
        """Close database connections"""
        if self._engine:
            self._engine.dispose()
            logger.info("Voice service database connections closed")


# Global database instance
voice_db = VoiceServiceDatabase()


# Convenience functions
def init_database() -> None:
    """Initialize voice service database"""
    voice_db.initialize()


def create_database_tables() -> None:
    """Create voice service database tables"""
    voice_db.create_tables()


def get_database_session() -> Generator[Session, None, None]:
    """Get voice service database session"""
    return voice_db.get_session()


def database_health_check() -> dict:
    """Get voice service database health status"""
    return voice_db.health_check()


def cleanup_database() -> dict:
    """Run database cleanup tasks"""
    return {
        "expired_cache_cleaned": voice_db.cleanup_expired_cache(),
        "inactive_sessions_cleaned": voice_db.cleanup_inactive_sessions()
    }


if __name__ == "__main__":
    # Basic database setup for testing
    logging.basicConfig(level=logging.INFO)

    print("Initializing voice service database...")
    init_database()

    print("Creating tables...")
    create_database_tables()

    print("Testing connection...")
    health = database_health_check()
    print(f"Health check: {health}")

    print("Voice service database setup complete!")