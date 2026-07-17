from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event, Thread
import time
from typing import Any, Callable, Mapping

from babyai.memory.voice_memory import VoiceMemory
from babyai.tools.voice_tool import TranscriptResult


class ScreenReader:
    risk_rating = "high"
    required_permissions = ("voice.screen.capture",)

    def __init__(
        self,
        voice_service_url: str,
        voice_memory: VoiceMemory,
        *,
        project_id: str | None = None,
        project_policy: Mapping[str, Any] | None = None,
        timeout_seconds: float = 600.0,
        capture_fn: Callable[[float, str], str] | None = None,
        request_fn: Callable[[str, str, Mapping[str, Any] | None, float], Mapping[str, Any]] | None = None,
    ) -> None:
        self.voice_service_url = str(voice_service_url or "").rstrip("/")
        if not self.voice_service_url:
            raise ValueError("voice_service_url must be non-empty")

        self.voice_memory = voice_memory
        self.project_id = str(project_id or voice_memory.project_id).strip()
        if not self.project_id:
            raise ValueError("project_id must be non-empty")

        self.project_policy = dict(project_policy or {})
        self.timeout_seconds = max(1.0, float(timeout_seconds or 600.0))
        self._capture_fn = capture_fn
        self._request_fn = request_fn

        self._stop_event = Event()
        self._worker: Thread | None = None

    def capture_and_transcribe(self, duration_seconds: float) -> TranscriptResult:
        self._require_screen_permission()
        duration = max(0.1, float(duration_seconds or 0.1))
        audio_path = self._capture_audio(duration)
        response = self._request_json(
            "POST",
            "/v1/voice/transcribe",
            {
                "audio_path": audio_path,
                "language": "",
                "project_id": self.project_id,
                "source": "screen",
                "local_files_only": True,
            },
        )

        result = TranscriptResult(
            transcript=str(response.get("transcript") or "").strip(),
            segments=[
                {str(k): v for k, v in item.items()}
                for item in list(response.get("segments") or [])
                if isinstance(item, Mapping)
            ],
            language_detected=str(response.get("language_detected") or response.get("language") or "").strip(),
            source="screen",
            audio_path=audio_path,
            metadata=dict(response.get("metadata") or {}),
        )
        self.voice_memory.save_transcript(
            {
                "audio_path": audio_path,
                "transcript": result.transcript,
                "segments": result.segments,
                "language_detected": result.language_detected,
                "source": "screen",
            }
        )
        return result

    def start_continuous(
        self,
        callback: Callable[[TranscriptResult], None],
        chunk_seconds: float = 5,
    ) -> None:
        if not callable(callback):
            raise ValueError("callback must be callable")
        if self._worker is not None and self._worker.is_alive():
            raise RuntimeError("continuous screen reader already running")

        self._stop_event.clear()

        def _loop() -> None:
            while not self._stop_event.is_set():
                try:
                    result = self.capture_and_transcribe(float(chunk_seconds))
                    callback(result)
                except Exception:
                    # Hard failures are propagated by stopping worker.
                    self._stop_event.set()
                    raise
                time.sleep(0.01)

        self._worker = Thread(target=_loop, daemon=True, name="screen-reader")
        self._worker.start()

    def stop_continuous(self) -> None:
        self._stop_event.set()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=2.0)
        self._worker = None

    def _capture_audio(self, duration_seconds: float) -> str:
        if self._capture_fn is not None:
            out = self._capture_fn(float(duration_seconds), self.project_id)
            clean = str(out or "").strip()
            if not clean:
                raise RuntimeError("capture_fn returned empty path")
            return clean

        try:
            import numpy as np
            import sounddevice as sd
            import soundfile as sf
        except Exception as exc:
            raise RuntimeError("screen capture dependencies are missing") from exc

        samplerate = 16000
        channels = 1
        frames = int(float(duration_seconds) * samplerate)

        recording = sd.rec(frames, samplerate=samplerate, channels=channels, dtype="float32")
        sd.wait()

        with NamedTemporaryFile(prefix="screen-capture-", suffix=".wav", delete=False) as tmp:
            temp_path = Path(tmp.name).resolve()
        sf.write(temp_path.as_posix(), np.asarray(recording), samplerate)
        return temp_path.as_posix()

    def _request_json(self, method: str, path: str, payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if self._request_fn is not None:
            out = self._request_fn(str(method).upper(), str(path), payload, float(self.timeout_seconds))
            if not isinstance(out, Mapping):
                raise RuntimeError("voice service response must be an object")
            return dict(out)

        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen
        import json

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

    def _require_screen_permission(self) -> None:
        if bool(self.project_policy.get("allow_screen_capture", False)):
            return
        raise PermissionError("screen capture blocked: set allow_screen_capture=true in project policy")
