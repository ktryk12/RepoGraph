"""Normalized Pydantic response models for MCP tool results.

All 7 types are serialized to JSON by FastMCP and returned as text content.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel


class HealthResponse(BaseModel):
    ok: bool
    service: str = "voice-runtime"


class ReadyResponse(BaseModel):
    ready: bool
    stt_loaded: bool
    tts_loaded: bool
    error: str | None = None


class CapabilitiesResponse(BaseModel):
    stt: dict[str, Any]
    tts: dict[str, Any]


class AssetValidationResponse(BaseModel):
    ok: bool
    issues: list[str]
    assets_dir: str


class TranscribeResponse(BaseModel):
    text: str
    language: str
    duration_ms: int


class SynthesizeResponse(BaseModel):
    audio_b64: str
    sample_rate: int
    duration_ms: int


class ConvertSpeechResponse(BaseModel):
    transcript: str
    audio_b64: str
    source_language: str
    target_language: str
    sample_rate: int
