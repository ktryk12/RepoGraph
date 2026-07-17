from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class VideoServiceSettings:
    # Service configuration
    host: str = field(default_factory=lambda: os.environ.get("VIDEO_SERVICE_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("VIDEO_SERVICE_PORT", "8130")))

    # Video rendering
    video_renderer: str = field(default_factory=lambda: os.environ.get("VIDEO_RENDERER", "stub"))
    video_renderer_api_key: str = field(default_factory=lambda: os.environ.get("VIDEO_RENDERER_API_KEY", ""))
    artifact_dir: str = field(default_factory=lambda: os.environ.get("BABYAI_ARTIFACT_STORE", "artifacts"))
    claude_model: str = field(default_factory=lambda: os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"))

    # Kafka configuration
    kafka_bootstrap_servers: str = field(default_factory=lambda: os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"))
    kafka_group_id: str = field(default_factory=lambda: os.environ.get("VIDEO_GROUP_ID", "video-service"))
    kafka_topic_in: str = field(default_factory=lambda: os.environ.get("VIDEO_TOPIC_IN", "content.video.request"))
    kafka_topic_out: str = field(default_factory=lambda: os.environ.get("VIDEO_TOPIC_OUT", "content.video.complete"))

    # Database configuration
    db_host: str = field(default_factory=lambda: os.environ.get("VIDEO_DB_HOST", "localhost"))
    db_port: str = field(default_factory=lambda: os.environ.get("VIDEO_DB_PORT", "5432"))
    db_name: str = field(default_factory=lambda: os.environ.get("VIDEO_DB_NAME", "video_service_db"))
    db_user: str = field(default_factory=lambda: os.environ.get("VIDEO_DB_USER", "video_service_user"))
    db_password: str = field(default_factory=lambda: os.environ.get("VIDEO_DB_PASSWORD", "video_service_password"))
    db_echo: bool = field(default_factory=lambda: os.environ.get("VIDEO_DB_ECHO", "false").lower() == "true")

    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))


def load_video_settings() -> VideoServiceSettings:
    return VideoServiceSettings()