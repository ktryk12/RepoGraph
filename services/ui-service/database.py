"""
UI Service Database Configuration

Dedicated database connection and management for ui-service.
Handles user sessions, dashboard state, and WebSocket data following database-per-service pattern.
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


class UIServiceDatabase:
    """
    Database manager for ui-service

    Manages dedicated database connection for UI operations and user session data.
    Isolated from other microservices following database-per-service pattern.
    """

    def __init__(self):
        self._engine: Optional[Engine] = None
        self._session_factory: Optional[sessionmaker] = None
        self._database_url = self._get_database_url()

    def _get_database_url(self) -> str:
        """Get database URL from environment variables"""
        # UI service specific database configuration
        host = os.getenv("UI_DB_HOST", "localhost")
        port = os.getenv("UI_DB_PORT", "5432")
        database = os.getenv("UI_DB_NAME", "ui_service_db")
        username = os.getenv("UI_DB_USER", "ui_service_user")
        password = os.getenv("UI_DB_PASSWORD", "ui_service_password")

        return f"postgresql://{username}:{password}@{host}:{port}/{database}"

    def initialize(self) -> None:
        """Initialize database connection and session factory"""
        try:
            self._engine = create_engine(
                self._database_url,
                poolclass=QueuePool,
                pool_size=10,  # UI service typically has more concurrent connections
                max_overflow=20,
                pool_timeout=30,
                pool_recycle=3600,
                echo=os.getenv("UI_DB_ECHO", "false").lower() == "true"
            )

            # Test connection
            with self._engine.connect() as conn:
                conn.execute("SELECT 1")
                logger.info("UI service database connection established")

            self._session_factory = sessionmaker(bind=self._engine)

        except Exception as e:
            logger.error(f"Failed to initialize UI service database: {e}")
            raise

    def create_tables(self) -> None:
        """Create all database tables"""
        if not self._engine:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        try:
            Base.metadata.create_all(self._engine)
            logger.info("UI service database tables created successfully")
        except Exception as e:
            logger.error(f"Failed to create UI service database tables: {e}")
            raise

    def drop_tables(self) -> None:
        """Drop all database tables (use with caution!)"""
        if not self._engine:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        try:
            Base.metadata.drop_all(self._engine)
            logger.warning("UI service database tables dropped")
        except Exception as e:
            logger.error(f"Failed to drop UI service database tables: {e}")
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
                        "database": "ui_service_db",
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
            logger.error(f"UI service database health check failed: {e}")
            return {"status": "unhealthy", "error": str(e)}

    def get_stats(self) -> dict:
        """Get database statistics"""
        try:
            with self.get_session() as session:
                # Import here to avoid circular imports
                from models import (UserSession, WebSocketConnection, DashboardWidget,
                                  UIEvent, Notification, UIMetric)

                stats = {
                    "user_sessions": {
                        "total": session.query(UserSession).count(),
                        "active": session.query(UserSession).filter(
                            UserSession.status == 'active'
                        ).count(),
                        "expired": session.query(UserSession).filter(
                            UserSession.status == 'expired'
                        ).count()
                    },
                    "websocket_connections": {
                        "total": session.query(WebSocketConnection).count(),
                        "connected": session.query(WebSocketConnection).filter(
                            WebSocketConnection.status == 'connected'
                        ).count(),
                        "disconnected": session.query(WebSocketConnection).filter(
                            WebSocketConnection.status == 'disconnected'
                        ).count()
                    },
                    "dashboard_widgets": {
                        "total": session.query(DashboardWidget).count(),
                        "visible": session.query(DashboardWidget).filter(
                            DashboardWidget.is_visible == True
                        ).count(),
                        "by_type": {}
                    },
                    "ui_events": {
                        "total": session.query(UIEvent).count(),
                        "errors": session.query(UIEvent).filter(
                            UIEvent.event_type == 'error'
                        ).count(),
                        "by_category": {}
                    },
                    "notifications": {
                        "total": session.query(Notification).count(),
                        "pending": session.query(Notification).filter(
                            Notification.status == 'pending'
                        ).count(),
                        "unread": session.query(Notification).filter(
                            Notification.is_read == False
                        ).count()
                    },
                    "ui_metrics": {
                        "total": session.query(UIMetric).count(),
                        "by_type": {}
                    }
                }

                return stats

        except Exception as e:
            logger.error(f"Failed to get UI service database stats: {e}")
            return {"error": str(e)}

    def cleanup_expired_sessions(self) -> int:
        """Clean up expired user sessions"""
        try:
            with self.get_session() as session:
                from models import UserSession
                from datetime import datetime

                expired_count = session.query(UserSession).filter(
                    UserSession.expires_at < datetime.utcnow()
                ).count()

                if expired_count > 0:
                    session.query(UserSession).filter(
                        UserSession.expires_at < datetime.utcnow()
                    ).update({"status": "expired"})
                    session.commit()
                    logger.info(f"Marked {expired_count} sessions as expired")

                return expired_count

        except Exception as e:
            logger.error(f"Failed to cleanup expired sessions: {e}")
            return 0

    def cleanup_old_events(self, days_old: int = 30) -> int:
        """Clean up old UI events"""
        try:
            with self.get_session() as session:
                from models import UIEvent
                from datetime import datetime, timedelta

                cutoff_date = datetime.utcnow() - timedelta(days=days_old)
                old_events_count = session.query(UIEvent).filter(
                    UIEvent.occurred_at < cutoff_date
                ).count()

                if old_events_count > 0:
                    session.query(UIEvent).filter(
                        UIEvent.occurred_at < cutoff_date
                    ).delete()
                    session.commit()
                    logger.info(f"Deleted {old_events_count} old UI events")

                return old_events_count

        except Exception as e:
            logger.error(f"Failed to cleanup old events: {e}")
            return 0

    def cleanup_old_metrics(self, days_old: int = 7) -> int:
        """Clean up old UI metrics"""
        try:
            with self.get_session() as session:
                from models import UIMetric
                from datetime import datetime, timedelta

                cutoff_date = datetime.utcnow() - timedelta(days=days_old)
                old_metrics_count = session.query(UIMetric).filter(
                    UIMetric.timestamp < cutoff_date
                ).count()

                if old_metrics_count > 0:
                    session.query(UIMetric).filter(
                        UIMetric.timestamp < cutoff_date
                    ).delete()
                    session.commit()
                    logger.info(f"Deleted {old_metrics_count} old UI metrics")

                return old_metrics_count

        except Exception as e:
            logger.error(f"Failed to cleanup old metrics: {e}")
            return 0

    def cleanup_dismissed_notifications(self, days_old: int = 7) -> int:
        """Clean up dismissed notifications"""
        try:
            with self.get_session() as session:
                from models import Notification
                from datetime import datetime, timedelta

                cutoff_date = datetime.utcnow() - timedelta(days=days_old)
                dismissed_count = session.query(Notification).filter(
                    Notification.dismissed_at < cutoff_date,
                    Notification.status == 'dismissed'
                ).count()

                if dismissed_count > 0:
                    session.query(Notification).filter(
                        Notification.dismissed_at < cutoff_date,
                        Notification.status == 'dismissed'
                    ).delete()
                    session.commit()
                    logger.info(f"Deleted {dismissed_count} dismissed notifications")

                return dismissed_count

        except Exception as e:
            logger.error(f"Failed to cleanup dismissed notifications: {e}")
            return 0

    def close(self) -> None:
        """Close database connections"""
        if self._engine:
            self._engine.dispose()
            logger.info("UI service database connections closed")


# Global database instance
ui_db = UIServiceDatabase()


# Convenience functions
def init_database() -> None:
    """Initialize UI service database"""
    ui_db.initialize()


def create_database_tables() -> None:
    """Create UI service database tables"""
    ui_db.create_tables()


def get_database_session() -> Generator[Session, None, None]:
    """Get UI service database session"""
    return ui_db.get_session()


def database_health_check() -> dict:
    """Get UI service database health status"""
    return ui_db.health_check()


def cleanup_database() -> dict:
    """Run database cleanup tasks"""
    return {
        "expired_sessions_cleaned": ui_db.cleanup_expired_sessions(),
        "old_events_cleaned": ui_db.cleanup_old_events(),
        "old_metrics_cleaned": ui_db.cleanup_old_metrics(),
        "dismissed_notifications_cleaned": ui_db.cleanup_dismissed_notifications()
    }


if __name__ == "__main__":
    # Basic database setup for testing
    logging.basicConfig(level=logging.INFO)

    print("Initializing UI service database...")
    init_database()

    print("Creating tables...")
    create_database_tables()

    print("Testing connection...")
    health = database_health_check()
    print(f"Health check: {health}")

    print("UI service database setup complete!")