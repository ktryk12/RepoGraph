"""Ren HTTP-klient til voice-service-proxy.

Følger præcist samme mønster som LlamaCppRunnerGateway:
  - urllib.request (ingen nye deps)
  - request_fn injectable (til test-mocking)
  - base_url fra env: VOICE_SERVICE_BASE_URL
  - timeout fra env: VOICE_TIMEOUT_SECONDS

Endpoint-tilpasning (fra aesa/api/voice_service.py):
  speak()     → POST /v1/voice/speak      → returnerer dict (JSON), ikke raw bytes
  transcribe()→ POST /v1/voice/transcribe → tager audio_path (str), ikke audio_bytes
  list_voices()→ GET /v1/voice/voices     → kræver ?project_id= query param
  is_available()→ GET /health             → returnerer aldrig exception
"""
from __future__ import annotations

import json
import os
import socket
from typing import Any, Callable, Dict, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


VoiceRequestFn = Callable[[str, str, Dict[str, Any] | None, float], Any]

_DEFAULT_BASE_URL = "http://voice-service-proxy:8111"
_DEFAULT_TIMEOUT = 30.0
_HEALTH_PATHS = ("/health", "/v1/voice/health")


class VoiceServiceError(RuntimeError):
    pass


class VoiceServiceTimeoutError(VoiceServiceError):
    def __init__(self, message: str, *, timeout_seconds: float) -> None:
        self.timeout_seconds = float(timeout_seconds)
        super().__init__(str(message))


class VoiceServiceClient:
    """HTTP-klient mod voice-service-proxy.

    Brug request_fn til test-mocking — samme mønster som LlamaCppRunnerGateway.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        request_fn: VoiceRequestFn | None = None,
    ) -> None:
        self._base_url = (
            str(base_url).rstrip("/")
            if base_url is not None
            else str(os.environ.get("VOICE_SERVICE_BASE_URL", _DEFAULT_BASE_URL)).rstrip("/")
        )
        raw_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else float(os.environ.get("VOICE_TIMEOUT_SECONDS", str(_DEFAULT_TIMEOUT)))
        )
        self._timeout_seconds = max(0.1, float(raw_timeout))
        self._request_fn = request_fn

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def speak(
        self,
        text: str,
        voice_id: str | None = None,
        *,
        project_id: str = "default",
        language: str = "da",
        local_files_only: bool = True,
    ) -> Dict[str, Any]:
        """POST /v1/voice/speak — returnerer JSON-response med file_path og metadata.

        Tilpasning fra spec: endpoint returnerer JSON (ikke raw audio bytes).
        Felter: {audio_id, file_path, duration, voice_id, language, metadata}
        """
        cleaned = str(text or "").strip()
        if not cleaned:
            raise VoiceServiceError("speak: text is required")

        payload: Dict[str, Any] = {
            "text": cleaned,
            "voice_profile": str(voice_id or "default").strip() or "default",
            "project_id": str(project_id),
            "language": str(language),
            "local_files_only": bool(local_files_only),
        }
        return self._request_json("POST", "/v1/voice/speak", payload)

    def transcribe(
        self,
        audio_path: str,
        *,
        project_id: str = "default",
        language: str | None = None,
        local_files_only: bool = True,
    ) -> str:
        """POST /v1/voice/transcribe — returnerer transskriberet tekst.

        Tilpasning fra spec: endpoint tager audio_path (str), ikke audio_bytes.
        """
        cleaned_path = str(audio_path or "").strip()
        if not cleaned_path:
            raise VoiceServiceError("transcribe: audio_path is required")

        payload: Dict[str, Any] = {
            "audio_path": cleaned_path,
            "project_id": str(project_id),
            "local_files_only": bool(local_files_only),
        }
        if language is not None:
            payload["language"] = str(language)

        response = self._request_json("POST", "/v1/voice/transcribe", payload)
        transcript = str(response.get("transcript") or response.get("text") or "").strip()
        return transcript

    def list_voices(self, project_id: str = "default") -> list[Dict[str, Any]]:
        """GET /v1/voice/voices?project_id= — returnerer liste af voice profiler."""
        path = f"/v1/voice/voices?project_id={str(project_id)}"
        response = self._request_json("GET", path, None)
        voices = response.get("voices")
        if isinstance(voices, list):
            return [dict(v) for v in voices if isinstance(v, Mapping)]
        return []

    def is_available(self) -> bool:
        """GET /health — returnerer False ved fejl. Rejser aldrig exception."""
        for path in _HEALTH_PATHS:
            try:
                payload = self._request_json("GET", path, None)
                if payload.get("ok") is not False:
                    return True
            except VoiceServiceTimeoutError:
                return False
            except VoiceServiceError:
                continue
            except Exception:
                continue
        return False

    # ------------------------------------------------------------------
    # Internal HTTP
    # ------------------------------------------------------------------

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Dict[str, Any] | None,
    ) -> Dict[str, Any]:
        if self._request_fn is not None:
            response = self._request_fn(
                str(method).upper(),
                str(path),
                dict(payload) if isinstance(payload, dict) else None,
                float(self._timeout_seconds),
            )
            if not isinstance(response, dict):
                raise VoiceServiceError("voice_service response must be an object")
            return response

        request_headers = {"accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
            request_headers["content-type"] = "application/json"

        req = Request(
            url=f"{self._base_url}{path}",
            data=body,
            headers=request_headers,
            method=str(method).upper(),
        )
        try:
            with urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace").strip()
                if not raw:
                    return {}
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
                return {"value": parsed}
        except HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")
            raise VoiceServiceError(
                f"voice_service_http_error status={exc.code} body={body_text}"
            ) from exc
        except TimeoutError as exc:
            raise VoiceServiceTimeoutError(
                f"voice_service_timeout timeout_seconds={self._timeout_seconds}",
                timeout_seconds=self._timeout_seconds,
            ) from exc
        except URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise VoiceServiceTimeoutError(
                    f"voice_service_timeout timeout_seconds={self._timeout_seconds}",
                    timeout_seconds=self._timeout_seconds,
                ) from exc
            raise VoiceServiceError(f"voice_service_unreachable: {exc}") from exc


def build_voice_service_client(
    *,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    request_fn: VoiceRequestFn | None = None,
) -> VoiceServiceClient:
    """Factory — samme default-mønster som resten af infrastructure-laget."""
    return VoiceServiceClient(
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        request_fn=request_fn,
    )
