from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class UIServiceSettings:
    # Service configuration
    host: str = field(default_factory=lambda: os.environ.get("UI_SERVICE_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("UI_SERVICE_PORT", "8080")))

    # WebSocket configuration
    websocket_enabled: bool = field(default_factory=lambda: os.environ.get("WEBSOCKET_ENABLED", "true").lower() == "true")
    websocket_port: int = field(default_factory=lambda: int(os.environ.get("WEBSOCKET_PORT", "8081")))

    # Session configuration
    session_timeout_minutes: int = field(default_factory=lambda: int(os.environ.get("SESSION_TIMEOUT_MINUTES", "60")))
    session_secret_key: str = field(default_factory=lambda: os.environ.get("SESSION_SECRET_KEY", "ui-service-secret"))

    # Dashboard configuration
    dashboard_refresh_interval_seconds: int = field(default_factory=lambda: int(os.environ.get("DASHBOARD_REFRESH_INTERVAL", "30")))
    max_dashboard_widgets: int = field(default_factory=lambda: int(os.environ.get("MAX_DASHBOARD_WIDGETS", "20")))

    # Kafka configuration
    kafka_bootstrap_servers: str = field(default_factory=lambda: os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"))
    kafka_group_id: str = field(default_factory=lambda: os.environ.get("UI_GROUP_ID", "ui-service"))

    # External service URLs
    request_gate_base_url: str = field(default_factory=lambda: os.environ.get("REQUEST_GATE_BASE_URL", "http://localhost:8097"))
    video_service_url: str = field(default_factory=lambda: os.environ.get("VIDEO_SERVICE_URL", "http://localhost:8130"))
    voice_service_url: str = field(default_factory=lambda: os.environ.get("VOICE_SERVICE_URL", "http://localhost:7080"))

    # Database configuration
    db_host: str = field(default_factory=lambda: os.environ.get("UI_DB_HOST", "localhost"))
    db_port: str = field(default_factory=lambda: os.environ.get("UI_DB_PORT", "5432"))
    db_name: str = field(default_factory=lambda: os.environ.get("UI_DB_NAME", "ui_service_db"))
    db_user: str = field(default_factory=lambda: os.environ.get("UI_DB_USER", "ui_service_user"))
    db_password: str = field(default_factory=lambda: os.environ.get("UI_DB_PASSWORD", "ui_service_password"))
    db_echo: bool = field(default_factory=lambda: os.environ.get("UI_DB_ECHO", "false").lower() == "true")

    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))


def load_ui_settings() -> UIServiceSettings:
    return UIServiceSettings()