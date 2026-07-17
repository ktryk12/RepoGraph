from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from babyai.memory.voice_memory import VoiceMemory, VoiceProfile
from babyai.tools.content_policy import ContentPolicy
from babyai.tools.registry import ToolDefinition, ToolRegistry


@dataclass(frozen=True)
class AudioResult:
    audio_id: str
    file_path: str
    text: str
    voice_id: str
    language: str
    duration_seconds: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class TranscriptResult:
    transcript: str
    segments: list[dict[str, Any]]
    language_detected: str
    source: str
    audio_path: str
    metadata: dict[str, Any]


class VoiceTool:
    def __init__(
        self,
        voice_service_url: str,
        content_policy: ContentPolicy,
        voice_memory: VoiceMemory,
        *,
        project_id: str | None = None,
        project_policy: Mapping[str, Any] | None = None,
        timeout_seconds: float = 600.0,
        request_fn: Callable[[str, str, Mapping[str, Any] | None, float], Mapping[str, Any]] | None = None,
    ) -> None:
        self.voice_service_url = str(voice_service_url or "").rstrip("/")
        if not self.voice_service_url:
            raise ValueError("voice_service_url must be non-empty")

        self.content_policy = content_policy
        self.voice_memory = voice_memory
        self.project_id = str(project_id or voice_memory.project_id).strip()
        if not self.project_id:
            raise ValueError("project_id must be non-empty")

        self.project_policy = dict(project_policy or {})
        self.timeout_seconds = max(1.0, float(timeout_seconds or 600.0))
        self._request_fn = request_fn

    def tool_definition(self, task_type: str) -> ToolDefinition:
        risk, permissions = _task_security(task_type)
        return ToolDefinition(
            id=f"voice_tool.{str(task_type).strip().lower()}",
            name="Voice Tool",
            type="voice",
            capability="voice_generation",
            risk_rating=risk,
            required_permissions=list(permissions),
            cost_model={"unit": "audio_or_transcript"},
            audit_hooks=["tool_call"],
        )

    def register_in_registry(self, registry: ToolRegistry, task_type: str) -> str:
        if not isinstance(registry, ToolRegistry):
            raise ValueError("registry must be ToolRegistry")
        return registry.register(self.tool_definition(task_type))

    def speak(
        self,
        text: str,
        voice_profile: str,
        language: str,
        sequence_id: str | None = None,
    ) -> AudioResult:
        clean_text = str(text or "").strip()
        if not clean_text:
            raise ValueError("text must be non-empty")

        self.content_policy.check(
            {
                "style_profile": "safe",
                "prompt": clean_text,
                "project_id": self.project_id,
                "project_policy": self.project_policy,
            }
        )

        payload = {
            "text": clean_text,
            "voice_profile": str(voice_profile or "default").strip() or "default",
            "language": str(language or "da").strip() or "da",
            "project_id": self.project_id,
            "sequence_id": sequence_id,
            "local_files_only": True,
        }
        response = self._request_json("POST", "/v1/voice/speak", payload)
        return self._to_audio_result(
            response=response,
            fallback_text=clean_text,
            fallback_voice=str(payload["voice_profile"]),
            fallback_language=str(payload["language"]),
            sequence_id=sequence_id,
        )

    def clone_voice(self, text: str, voice_id: str, language: str) -> AudioResult:
        clean_text = str(text or "").strip()
        clean_voice_id = str(voice_id or "").strip()
        if not clean_text:
            raise ValueError("text must be non-empty")
        if not clean_voice_id:
            raise ValueError("voice_id must be non-empty")

        _ = self.voice_memory.get_voice(clean_voice_id)
        self.content_policy.check(
            {
                "style_profile": "safe",
                "prompt": clean_text,
                "project_id": self.project_id,
                "project_policy": self.project_policy,
            }
        )

        payload = {
            "text": clean_text,
            "voice_id": clean_voice_id,
            "language": str(language or "da").strip() or "da",
            "project_id": self.project_id,
            "local_files_only": True,
        }
        response = self._request_json("POST", "/v1/voice/clone", payload)
        return self._to_audio_result(
            response=response,
            fallback_text=clean_text,
            fallback_voice=clean_voice_id,
            fallback_language=str(payload["language"]),
            sequence_id=None,
        )

    def transcribe(self, audio_path: str, language: str | None = None) -> TranscriptResult:
        clean_audio_path = str(audio_path or "").strip()
        if not clean_audio_path:
            raise ValueError("audio_path must be non-empty")

        payload = {
            "audio_path": clean_audio_path,
            "language": str(language or "").strip(),
            "project_id": self.project_id,
            "local_files_only": True,
        }
        response = self._request_json("POST", "/v1/voice/transcribe", payload)

        segments_raw = response.get("segments")
        segments: list[dict[str, Any]] = []
        if isinstance(segments_raw, list):
            for item in segments_raw:
                if isinstance(item, Mapping):
                    segments.append({str(k): v for k, v in item.items()})

        return TranscriptResult(
            transcript=str(response.get("transcript") or "").strip(),
            segments=segments,
            language_detected=str(response.get("language_detected") or response.get("language") or "").strip(),
            source=str(response.get("source") or "file").strip(),
            audio_path=clean_audio_path,
            metadata=dict(response.get("metadata") or {}),
        )

    def register_voice(self, name: str, sample_path: str, language: str) -> str:
        clean_name = str(name or "").strip()
        clean_sample = str(sample_path or "").strip()
        if not clean_name:
            raise ValueError("name must be non-empty")
        if not clean_sample:
            raise ValueError("sample_path must be non-empty")
        if not Path(clean_sample).expanduser().exists():
            raise FileNotFoundError(f"sample_path not found: {clean_sample}")

        payload = {
            "name": clean_name,
            "speaker_wav_path": clean_sample,
            "language": str(language or "da").strip() or "da",
            "project_id": self.project_id,
            "local_files_only": True,
        }
        response = self._request_json("POST", "/v1/voice/register", payload)
        voice_id = str(response.get("voice_id") or "").strip()
        if not voice_id:
            raise RuntimeError("voice register response missing voice_id")
        return voice_id

    def list_voices(self) -> list[VoiceProfile]:
        query = urlencode({"project_id": self.project_id})
        path = f"/v1/voice/voices?{query}"
        response = self._request_json("GET", path, payload=None)
        voices_raw = response.get("voices")
        if not isinstance(voices_raw, list):
            return []

        out: list[VoiceProfile] = []
        for item in voices_raw:
            if not isinstance(item, Mapping):
                continue
            out.append(
                VoiceProfile(
                    id=str(item.get("id") or "").strip(),
                    project_id=str(item.get("project_id") or self.project_id).strip(),
                    name=str(item.get("name") or "").strip(),
                    sample_path=str(item.get("sample_path") or "").strip(),
                    language=str(item.get("language") or "").strip(),
                    created_at=str(item.get("created_at") or "").strip(),
                )
            )
        return out

    def _to_audio_result(
        self,
        *,
        response: Mapping[str, Any],
        fallback_text: str,
        fallback_voice: str,
        fallback_language: str,
        sequence_id: str | None,
    ) -> AudioResult:
        file_path = str(response.get("file_path") or "").strip()
        if not file_path:
            raise RuntimeError("voice_service response missing file_path")

        voice_id = str(response.get("voice_id") or response.get("voice_profile") or fallback_voice).strip() or fallback_voice
        language = str(response.get("language") or fallback_language).strip() or fallback_language
        text = str(response.get("text") or fallback_text).strip() or fallback_text
        duration_seconds = _to_non_negative_float(response.get("duration") or response.get("duration_seconds"), default=0.0)

        audio_id = str(response.get("audio_id") or "").strip()
        if not audio_id:
            audio_id = self.voice_memory.save(
                {
                    "file_path": file_path,
                    "text": text,
                    "voice_id": voice_id,
                    "language": language,
                    "duration_seconds": duration_seconds,
                    "domain": "voice",
                    "sequence_id": sequence_id,
                },
                sequence_id=sequence_id,
            )

        return AudioResult(
            audio_id=audio_id,
            file_path=file_path,
            text=text,
            voice_id=voice_id,
            language=language,
            duration_seconds=duration_seconds,
            metadata=dict(response.get("metadata") or {}),
        )

    def _request_json(self, method: str, path: str, payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if self._request_fn is not None:
            out = self._request_fn(str(method).upper(), str(path), payload, float(self.timeout_seconds))
            if not isinstance(out, Mapping):
                raise RuntimeError("voice service response must be an object")
            return dict(out)

        body = None if payload is None else json.dumps(dict(payload), ensure_ascii=True, sort_keys=True).encode("utf-8")
        headers = {"accept": "application/json"}
        if body is not None:
            headers["content-type"] = "application/json"
        req = Request(
            url=f"{self.voice_service_url}{str(path)}",
            data=body,
            method=str(method).upper(),
            headers=headers,
        )
        try:
            with urlopen(req, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                decoded = json.loads(raw) if raw.strip() else {}
                if not isinstance(decoded, Mapping):
                    raise RuntimeError("voice service response must be an object")
                return dict(decoded)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"voice_service_http_error status={int(exc.code)} body={detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"voice_service_unreachable: {exc}") from exc


def _task_security(task_type: str) -> tuple[str, tuple[str, ...]]:
    clean = str(task_type or "speak").strip().lower()
    if clean in {"speak", "tts"}:
        return "low", ("voice.tts",)
    if clean in {"clone", "clone_voice"}:
        return "medium", ("voice.clone",)
    if clean in {"screen", "screen_capture", "screen_transcribe"}:
        return "high", ("voice.screen.capture",)
    if clean in {"transcribe", "stt"}:
        return "low", ("voice.stt",)
    return "low", ("voice.tts",)


def _to_non_negative_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
        if parsed < 0:
            return float(default)
        return parsed
    except Exception:
        return float(default)
