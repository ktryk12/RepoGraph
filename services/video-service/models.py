"""
Video Service Database Models

Dedicated database models for video-service following database-per-service pattern.
No shared database dependencies with other microservices.
"""

from sqlalchemy import Column, String, DateTime, Text, Integer, Float, Boolean, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid

Base = declarative_base()


class VideoJob(Base):
    """
    Video generation job model for video-service database
    """
    __tablename__ = 'video_jobs'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Job metadata
    status = Column(String(50), nullable=False, default='created')  # created, processing, completed, failed
    job_type = Column(String(50), nullable=False)  # script_generation, video_rendering, etc.
    priority = Column(Integer, nullable=False, default=5)  # 1-10, higher is higher priority

    # Content
    script_content = Column(Text, nullable=True)
    video_prompt = Column(Text, nullable=True)

    # Video parameters
    duration_seconds = Column(Integer, nullable=True)
    resolution = Column(String(20), nullable=True)  # "1080p", "720p", "4K"
    style = Column(String(100), nullable=True)  # "professional", "casual", "animated"
    frame_rate = Column(Integer, nullable=True, default=30)

    # External provider info
    provider = Column(String(50), nullable=True)  # "runway_ml", "synthesia", "heygen"
    provider_job_id = Column(String(200), nullable=True)
    provider_metadata = Column(JSON, nullable=True)

    # Output
    output_video_url = Column(String(500), nullable=True)
    output_video_path = Column(String(500), nullable=True)
    output_file_size = Column(Integer, nullable=True)  # bytes

    # Processing metrics
    processing_started_at = Column(DateTime, nullable=True)
    processing_completed_at = Column(DateTime, nullable=True)
    processing_duration_seconds = Column(Float, nullable=True)

    # Error handling
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Soft delete
    deleted_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<VideoJob(id={self.id}, status={self.status}, job_type={self.job_type})>"


class VideoScript(Base):
    """
    Generated video scripts for video-service
    """
    __tablename__ = 'video_scripts'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Content
    title = Column(String(200), nullable=False)
    content = Column(Text, nullable=False)
    prompt_used = Column(Text, nullable=True)

    # Metadata
    estimated_duration = Column(Integer, nullable=True)  # seconds
    target_audience = Column(String(100), nullable=True)
    style = Column(String(100), nullable=True)
    language = Column(String(10), nullable=False, default='en')

    # AI generation metadata
    model_used = Column(String(100), nullable=True)  # "claude-4", "gpt-4", etc.
    generation_parameters = Column(JSON, nullable=True)

    # Quality metrics
    readability_score = Column(Float, nullable=True)
    engagement_score = Column(Float, nullable=True)

    # Usage tracking
    times_used = Column(Integer, nullable=False, default=0)
    last_used_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<VideoScript(id={self.id}, title={self.title[:50]})>"


class VideoAsset(Base):
    """
    Video assets and files managed by video-service
    """
    __tablename__ = 'video_assets'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # File information
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=True)  # bytes
    mime_type = Column(String(100), nullable=True)

    # Video metadata
    duration_seconds = Column(Float, nullable=True)
    resolution_width = Column(Integer, nullable=True)
    resolution_height = Column(Integer, nullable=True)
    frame_rate = Column(Float, nullable=True)
    bitrate = Column(Integer, nullable=True)
    codec = Column(String(50), nullable=True)

    # Classification
    asset_type = Column(String(50), nullable=False)  # "final_video", "preview", "thumbnail", "source_material"
    category = Column(String(100), nullable=True)  # "marketing", "educational", "entertainment"

    # Relationships (stored as UUIDs for loose coupling)
    related_job_id = Column(UUID(as_uuid=True), nullable=True)
    related_script_id = Column(UUID(as_uuid=True), nullable=True)

    # Storage metadata
    storage_provider = Column(String(50), nullable=False, default='local')  # "local", "s3", "gcs"
    storage_region = Column(String(50), nullable=True)
    storage_metadata = Column(JSON, nullable=True)

    # Access control
    is_public = Column(Boolean, nullable=False, default=False)
    access_url = Column(String(500), nullable=True)
    expires_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<VideoAsset(id={self.id}, filename={self.filename}, type={self.asset_type})>"


class VideoProvider(Base):
    """
    External video provider configurations for video-service
    """
    __tablename__ = 'video_providers'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Provider info
    provider_name = Column(String(100), nullable=False, unique=True)  # "runway_ml", "synthesia"
    display_name = Column(String(100), nullable=False)
    provider_url = Column(String(200), nullable=True)

    # Configuration
    api_endpoint = Column(String(200), nullable=False)
    api_version = Column(String(20), nullable=True)
    authentication_type = Column(String(50), nullable=False)  # "api_key", "oauth", "basic"

    # Capabilities
    supported_resolutions = Column(JSON, nullable=True)  # ["1080p", "720p", "4K"]
    supported_formats = Column(JSON, nullable=True)  # ["mp4", "mov", "avi"]
    max_duration_seconds = Column(Integer, nullable=True)
    max_file_size_mb = Column(Integer, nullable=True)

    # Operational
    is_enabled = Column(Boolean, nullable=False, default=True)
    rate_limit_per_minute = Column(Integer, nullable=True)
    cost_per_minute = Column(Float, nullable=True)  # cost estimation

    # Health monitoring
    last_health_check = Column(DateTime, nullable=True)
    health_status = Column(String(20), nullable=True)  # "healthy", "degraded", "down"
    response_time_ms = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<VideoProvider(name={self.provider_name}, enabled={self.is_enabled})>"


# Database schema version for migrations
SCHEMA_VERSION = "1.0.0"