from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    host: str = field(default_factory=lambda: os.environ.get("VOICE_RUNTIME_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("VOICE_RUNTIME_PORT", "7080")))

    # STT
    stt_model: str = field(default_factory=lambda: os.environ.get("STT_MODEL", "openai/whisper-base"))
    stt_device: str = field(default_factory=lambda: os.environ.get("STT_DEVICE", "cpu"))

    # TTS
    tts_model: str = field(default_factory=lambda: os.environ.get("TTS_MODEL", "tts_models/multilingual/multi-dataset/xtts_v2"))
    tts_device: str = field(default_factory=lambda: os.environ.get("TTS_DEVICE", "cpu"))
    tts_speaker: str = field(default_factory=lambda: os.environ.get("TTS_SPEAKER", "Claribel Dervla"))

    # Asset validation
    model_assets_dir: str = field(default_factory=lambda: os.environ.get("MODEL_ASSETS_DIR", "/models"))

    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))

    # Database configuration
    db_host: str = field(default_factory=lambda: os.environ.get("VOICE_DB_HOST", "localhost"))
    db_port: str = field(default_factory=lambda: os.environ.get("VOICE_DB_PORT", "5432"))
    db_name: str = field(default_factory=lambda: os.environ.get("VOICE_DB_NAME", "voice_service_db"))
    db_user: str = field(default_factory=lambda: os.environ.get("VOICE_DB_USER", "voice_service_user"))
    db_password: str = field(default_factory=lambda: os.environ.get("VOICE_DB_PASSWORD", "voice_service_password"))
    db_echo: bool = field(default_factory=lambda: os.environ.get("VOICE_DB_ECHO", "false").lower() == "true")


def load_settings() -> Settings:
    return Settings()
