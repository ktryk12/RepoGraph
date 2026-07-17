"""
Voice Service Database Models

Dedicated database models for voice-service following database-per-service pattern.
Handles STT/TTS processing data and MCP server integration.
"""

from sqlalchemy import Column, String, DateTime, Text, Integer, Float, Boolean, JSON, LargeBinary
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime
import uuid

Base = declarative_base()


class VoiceJob(Base):
    """
    Voice processing job model for voice-service database
    """
    __tablename__ = 'voice_jobs'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Job metadata
    status = Column(String(50), nullable=False, default='created')  # created, processing, completed, failed
    job_type = Column(String(50), nullable=False)  # "stt", "tts", "voice_cloning", "noise_reduction"
    priority = Column(Integer, nullable=False, default=5)

    # Input data
    input_text = Column(Text, nullable=True)  # For TTS
    input_audio_path = Column(String(500), nullable=True)  # For STT
    input_audio_format = Column(String(20), nullable=True)  # "wav", "mp3", "flac"
    input_duration_seconds = Column(Float, nullable=True)

    # Processing parameters
    target_language = Column(String(10), nullable=False, default='en')
    voice_model = Column(String(100), nullable=True)  # "natural", "robotic", "custom_123"
    speech_rate = Column(Float, nullable=True, default=1.0)  # 0.5 = slow, 2.0 = fast
    pitch_adjustment = Column(Float, nullable=True, default=0.0)  # -12.0 to +12.0 semitones

    # STT specific
    enable_punctuation = Column(Boolean, nullable=False, default=True)
    confidence_threshold = Column(Float, nullable=False, default=0.8)
    speaker_diarization = Column(Boolean, nullable=False, default=False)

    # TTS specific
    voice_gender = Column(String(20), nullable=True)  # "male", "female", "neutral"
    emotion = Column(String(50), nullable=True)  # "neutral", "happy", "sad", "excited"
    output_format = Column(String(20), nullable=False, default='wav')

    # Results
    output_text = Column(Text, nullable=True)  # STT result
    output_audio_path = Column(String(500), nullable=True)  # TTS result
    confidence_score = Column(Float, nullable=True)
    processing_metadata = Column(JSON, nullable=True)

    # Model information
    model_used = Column(String(100), nullable=True)
    model_version = Column(String(50), nullable=True)
    processing_backend = Column(String(50), nullable=True)  # "whisper", "wav2vec2", "tacotron2"

    # Performance metrics
    processing_started_at = Column(DateTime, nullable=True)
    processing_completed_at = Column(DateTime, nullable=True)
    processing_duration_seconds = Column(Float, nullable=True)
    cpu_usage_percent = Column(Float, nullable=True)
    memory_usage_mb = Column(Integer, nullable=True)

    # Error handling
    error_message = Column(Text, nullable=True)
    error_code = Column(String(50), nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<VoiceJob(id={self.id}, type={self.job_type}, status={self.status})>"


class VoiceModel(Base):
    """
    Voice models registry for voice-service
    """
    __tablename__ = 'voice_models'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Model identification
    model_name = Column(String(100), nullable=False, unique=True)
    model_type = Column(String(50), nullable=False)  # "stt", "tts", "voice_clone"
    model_version = Column(String(50), nullable=False)

    # Model files
    model_path = Column(String(500), nullable=False)
    config_path = Column(String(500), nullable=True)
    checkpoint_path = Column(String(500), nullable=True)
    model_size_mb = Column(Integer, nullable=True)

    # Capabilities
    supported_languages = Column(JSON, nullable=False)  # ["en", "es", "fr", "de"]
    supported_sample_rates = Column(JSON, nullable=False)  # [16000, 22050, 44100]
    max_audio_length_seconds = Column(Integer, nullable=True)
    min_audio_length_seconds = Column(Float, nullable=True)

    # Performance characteristics
    typical_processing_speed = Column(Float, nullable=True)  # realtime factor (1.0 = realtime)
    memory_requirement_mb = Column(Integer, nullable=True)
    gpu_required = Column(Boolean, nullable=False, default=False)
    cpu_cores_recommended = Column(Integer, nullable=False, default=1)

    # Quality metrics
    accuracy_score = Column(Float, nullable=True)  # 0.0 - 1.0
    mos_score = Column(Float, nullable=True)  # Mean Opinion Score for TTS
    latency_ms = Column(Integer, nullable=True)

    # Operational
    is_enabled = Column(Boolean, nullable=False, default=True)
    is_loaded = Column(Boolean, nullable=False, default=False)
    load_priority = Column(Integer, nullable=False, default=5)
    warmup_time_seconds = Column(Float, nullable=True)

    # Health monitoring
    last_health_check = Column(DateTime, nullable=True)
    health_status = Column(String(20), nullable=True)  # "healthy", "degraded", "error"
    error_count = Column(Integer, nullable=False, default=0)
    last_error = Column(Text, nullable=True)

    # Usage statistics
    times_used = Column(Integer, nullable=False, default=0)
    total_processing_time = Column(Float, nullable=False, default=0.0)
    last_used_at = Column(DateTime, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<VoiceModel(name={self.model_name}, type={self.model_type}, enabled={self.is_enabled})>"


class AudioAsset(Base):
    """
    Audio files and assets managed by voice-service
    """
    __tablename__ = 'audio_assets'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # File information
    filename = Column(String(255), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=True)  # bytes
    mime_type = Column(String(100), nullable=True)

    # Audio metadata
    duration_seconds = Column(Float, nullable=True)
    sample_rate = Column(Integer, nullable=True)
    channels = Column(Integer, nullable=True)  # 1 = mono, 2 = stereo
    bit_depth = Column(Integer, nullable=True)  # 16, 24, 32
    bitrate = Column(Integer, nullable=True)
    codec = Column(String(50), nullable=True)

    # Classification
    asset_type = Column(String(50), nullable=False)  # "input", "output", "intermediate", "reference"
    content_type = Column(String(100), nullable=True)  # "speech", "music", "noise", "silence"
    language = Column(String(10), nullable=True)

    # Audio analysis
    volume_rms = Column(Float, nullable=True)
    volume_peak = Column(Float, nullable=True)
    signal_to_noise_ratio = Column(Float, nullable=True)
    spectral_analysis = Column(JSON, nullable=True)

    # Relationships (stored as UUIDs for loose coupling)
    related_job_id = Column(UUID(as_uuid=True), nullable=True)
    source_job_id = Column(UUID(as_uuid=True), nullable=True)

    # Storage metadata
    storage_provider = Column(String(50), nullable=False, default='local')
    storage_region = Column(String(50), nullable=True)
    storage_metadata = Column(JSON, nullable=True)

    # Access control
    is_temporary = Column(Boolean, nullable=False, default=True)
    expires_at = Column(DateTime, nullable=True)
    access_count = Column(Integer, nullable=False, default=0)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<AudioAsset(id={self.id}, filename={self.filename}, type={self.asset_type})>"


class MCPSession(Base):
    """
    MCP (Model Context Protocol) sessions for Claude Desktop integration
    """
    __tablename__ = 'mcp_sessions'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Session metadata
    session_id = Column(String(100), nullable=False, unique=True)
    client_id = Column(String(100), nullable=True)
    client_version = Column(String(50), nullable=True)

    # Session state
    status = Column(String(50), nullable=False, default='active')  # active, paused, terminated
    capabilities = Column(JSON, nullable=True)
    resources_offered = Column(JSON, nullable=True)

    # Activity tracking
    request_count = Column(Integer, nullable=False, default=0)
    last_request_at = Column(DateTime, nullable=True)
    total_processing_time = Column(Float, nullable=False, default=0.0)

    # Connection info
    connection_type = Column(String(50), nullable=False, default='stdio')  # stdio, websocket, tcp
    remote_address = Column(String(100), nullable=True)
    user_agent = Column(String(200), nullable=True)

    # Security
    authentication_method = Column(String(50), nullable=True)
    permissions = Column(JSON, nullable=True)
    rate_limit_remaining = Column(Integer, nullable=True)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_activity_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    def __repr__(self):
        return f"<MCPSession(session_id={self.session_id}, status={self.status})>"


class VoiceCache(Base):
    """
    Cache for voice processing results to improve performance
    """
    __tablename__ = 'voice_cache'

    # Primary key
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Cache key (hash of input parameters)
    cache_key = Column(String(64), nullable=False, unique=True, index=True)
    cache_type = Column(String(50), nullable=False)  # "stt_result", "tts_audio", "model_output"

    # Input hash for validation
    input_hash = Column(String(64), nullable=False)
    parameters_hash = Column(String(64), nullable=False)

    # Cached data
    cached_result = Column(JSON, nullable=True)  # For text results
    cached_file_path = Column(String(500), nullable=True)  # For audio files
    file_size = Column(Integer, nullable=True)

    # Cache metadata
    model_used = Column(String(100), nullable=False)
    model_version = Column(String(50), nullable=False)
    processing_time_saved = Column(Float, nullable=True)

    # Cache management
    access_count = Column(Integer, nullable=False, default=0)
    last_accessed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)
    priority = Column(Integer, nullable=False, default=5)

    # Timestamps
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<VoiceCache(key={self.cache_key}, type={self.cache_type})>"


# Database schema version for migrations
SCHEMA_VERSION = "1.0.0"