from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import shutil
import sqlite3
from threading import RLock
from typing import Any, Mapping
from uuid import uuid4

from babyai_shared.storage.safe_paths import safe_segment


@dataclass(frozen=True)
class VoiceEntry:
    id: str
    project_id: str
    domain: str
    file_path: str
    text: str
    voice_id: str
    language: str
    duration_seconds: float
    sequence_id: str
    sequence_position: int
    created_at: str


@dataclass(frozen=True)
class VoiceProfile:
    id: str
    project_id: str
    name: str
    sample_path: str
    language: str
    created_at: str


@dataclass(frozen=True)
class TranscriptResult:
    id: str
    project_id: str
    audio_path: str
    transcript: str
    segments: list[dict[str, Any]]
    language_detected: str
    source: str
    created_at: str


class VoiceMemory:
    def __init__(
        self,
        project_id: str,
        storage_path: str | Path = "outputs",
        db_ref: str | Path = "state/babyai_memory.sqlite",
        artifact_root: str | Path = "artifacts",
    ) -> None:
        self.project_id = str(project_id or "").strip()
        if not self.project_id:
            raise ValueError("project_id must be non-empty")

        self._storage_path = Path(storage_path).expanduser().resolve()
        self._storage_path.mkdir(parents=True, exist_ok=True)
        self._artifact_root = Path(artifact_root).expanduser().resolve()
        self._artifact_root.mkdir(parents=True, exist_ok=True)
        self._db_path = _resolve_db_path(db_ref)
        self._lock = RLock()
        self._init_schema()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def audio_root(self) -> Path:
        return self._storage_path / safe_segment(self.project_id) / "audio"

    def save(self, result: Mapping[str, Any], sequence_id: str | None = None) -> str:
        if not isinstance(result, Mapping):
            raise ValueError("result must be a mapping")

        source_path = Path(str(result.get("file_path") or "").strip()).expanduser()
        if not source_path.exists():
            raise FileNotFoundError(f"audio source file not found: {source_path}")

        sequence = str(sequence_id or result.get("sequence_id") or "standalone").strip() or "standalone"
        sequence_safe = safe_segment(sequence)

        text = str(result.get("text") or "").strip()
        voice_id = str(result.get("voice_id") or "default").strip() or "default"
        language = str(result.get("language") or "unknown").strip() or "unknown"
        duration_seconds = _to_non_negative_float(result.get("duration_seconds"), default=0.0)
        domain = str(result.get("domain") or "voice").strip() or "voice"

        with self._lock:
            position = self._next_sequence_position(sequence_safe)
            target_dir = self.audio_root / sequence_safe
            target_dir.mkdir(parents=True, exist_ok=True)
            ext = source_path.suffix.lower() or ".wav"
            target_name = f"{position:03d}{ext}"
            target_path = (target_dir / target_name).resolve()
            if source_path.resolve() != target_path:
                shutil.copy2(source_path, target_path)

            audio_id = f"audio-{uuid4().hex[:12]}"
            created_at = _utc_now_iso()
            self._execute(
                """
                INSERT INTO voice_entries (
                    id,
                    project_id,
                    domain,
                    file_path,
                    text,
                    voice_id,
                    language,
                    duration_seconds,
                    sequence_id,
                    sequence_position,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audio_id,
                    self.project_id,
                    domain,
                    target_path.as_posix(),
                    text,
                    voice_id,
                    language,
                    float(duration_seconds),
                    sequence_safe,
                    int(position),
                    created_at,
                ),
            )

            from verify.artifacts.registry import ArtifactRegistry
            registry = ArtifactRegistry(manifest_path=self._artifact_root / "registry" / "manifest.jsonl")
            registry.register(
                artifact_type="voice_asset",
                path=target_path,
                fingerprint=_sha256_file(target_path),
                metadata={
                    "project_id": self.project_id,
                    "voice_id": voice_id,
                    "language": language,
                    "sequence_id": sequence_safe,
                    "sequence_position": int(position),
                },
            )
            return audio_id

    def save_transcript(self, result: Mapping[str, Any]) -> str:
        if not isinstance(result, Mapping):
            raise ValueError("result must be a mapping")

        audio_path = str(result.get("audio_path") or "").strip()
        transcript = str(result.get("transcript") or "").strip()
        segments = _normalize_segments(result.get("segments"))
        language_detected = str(result.get("language_detected") or result.get("language") or "").strip()
        source = str(result.get("source") or "file").strip().lower() or "file"
        if source not in {"file", "screen"}:
            raise ValueError("source must be one of: file, screen")

        transcript_id = f"transcript-{uuid4().hex[:12]}"
        created_at = _utc_now_iso()
        self._execute(
            """
            INSERT INTO transcripts (
                id,
                project_id,
                audio_path,
                transcript,
                segments,
                language_detected,
                source,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                transcript_id,
                self.project_id,
                audio_path,
                transcript,
                json.dumps(segments, ensure_ascii=True, sort_keys=True),
                language_detected,
                source,
                created_at,
            ),
        )
        return transcript_id

    def save_voice_profile(self, name: str, sample_path: str, language: str) -> str:
        clean_name = str(name or "").strip()
        if not clean_name:
            raise ValueError("name must be non-empty")
        source_path = Path(str(sample_path or "").strip()).expanduser()
        if not source_path.exists():
            raise FileNotFoundError(f"voice sample file not found: {source_path}")

        language_clean = str(language or "unknown").strip() or "unknown"
        voice_id = f"voice-{uuid4().hex[:12]}"
        created_at = _utc_now_iso()

        profiles_dir = self.audio_root / "profiles"
        profiles_dir.mkdir(parents=True, exist_ok=True)
        ext = source_path.suffix.lower() or ".wav"
        target_path = (profiles_dir / f"{safe_segment(voice_id)}{ext}").resolve()
        if source_path.resolve() != target_path:
            shutil.copy2(source_path, target_path)

        self._execute(
            """
            INSERT INTO voice_profiles (
                id,
                project_id,
                name,
                sample_path,
                language,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                voice_id,
                self.project_id,
                clean_name,
                target_path.as_posix(),
                language_clean,
                created_at,
            ),
        )
        return voice_id

    def get_voice(self, voice_id: str) -> VoiceProfile:
        clean_id = str(voice_id or "").strip()
        if not clean_id:
            raise ValueError("voice_id must be non-empty")
        row = self._fetchone(
            """
            SELECT id, project_id, name, sample_path, language, created_at
            FROM voice_profiles
            WHERE id = ? AND project_id = ?
            """,
            (clean_id, self.project_id),
        )
        if row is None:
            raise ValueError(f"voice profile not found: {clean_id}")
        return VoiceProfile(
            id=str(row["id"]),
            project_id=str(row["project_id"]),
            name=str(row["name"]),
            sample_path=str(row["sample_path"]),
            language=str(row["language"]),
            created_at=str(row["created_at"]),
        )

    def list_voices(self) -> list[VoiceProfile]:
        rows = self._fetchall(
            """
            SELECT id, project_id, name, sample_path, language, created_at
            FROM voice_profiles
            WHERE project_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (self.project_id,),
        )
        out: list[VoiceProfile] = []
        for row in rows:
            out.append(
                VoiceProfile(
                    id=str(row["id"]),
                    project_id=str(row["project_id"]),
                    name=str(row["name"]),
                    sample_path=str(row["sample_path"]),
                    language=str(row["language"]),
                    created_at=str(row["created_at"]),
                )
            )
        return out

    def get_sequence(self, sequence_id: str) -> list[VoiceEntry]:
        clean_sequence = safe_segment(str(sequence_id or "").strip())
        if not clean_sequence:
            raise ValueError("sequence_id must be non-empty")

        rows = self._fetchall(
            """
            SELECT
                id,
                project_id,
                domain,
                file_path,
                text,
                voice_id,
                language,
                duration_seconds,
                sequence_id,
                sequence_position,
                created_at
            FROM voice_entries
            WHERE project_id = ? AND sequence_id = ?
            ORDER BY sequence_position ASC, created_at ASC, id ASC
            """,
            (self.project_id, clean_sequence),
        )
        out: list[VoiceEntry] = []
        for row in rows:
            out.append(
                VoiceEntry(
                    id=str(row["id"]),
                    project_id=str(row["project_id"]),
                    domain=str(row["domain"]),
                    file_path=str(row["file_path"]),
                    text=str(row["text"]),
                    voice_id=str(row["voice_id"]),
                    language=str(row["language"]),
                    duration_seconds=float(row["duration_seconds"]),
                    sequence_id=str(row["sequence_id"]),
                    sequence_position=int(row["sequence_position"]),
                    created_at=str(row["created_at"]),
                )
            )
        return out

    def get_transcripts(self, project_id: str, limit: int = 10) -> list[TranscriptResult]:
        clean_project_id = str(project_id or "").strip()
        if clean_project_id != self.project_id:
            raise ValueError("project_id mismatch for VoiceMemory instance")

        n = max(1, int(limit or 10))
        rows = self._fetchall(
            """
            SELECT
                id,
                project_id,
                audio_path,
                transcript,
                segments,
                language_detected,
                source,
                created_at
            FROM transcripts
            WHERE project_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (self.project_id, int(n)),
        )
        out: list[TranscriptResult] = []
        for row in rows:
            segments = _loads_json(row["segments"], fallback=[])
            if not isinstance(segments, list):
                segments = []
            out.append(
                TranscriptResult(
                    id=str(row["id"]),
                    project_id=str(row["project_id"]),
                    audio_path=str(row["audio_path"]),
                    transcript=str(row["transcript"]),
                    segments=[dict(item) for item in segments if isinstance(item, Mapping)],
                    language_detected=str(row["language_detected"]),
                    source=str(row["source"]),
                    created_at=str(row["created_at"]),
                )
            )
        return out

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS voice_entries (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    text TEXT NOT NULL,
                    voice_id TEXT NOT NULL,
                    language TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    sequence_id TEXT NOT NULL,
                    sequence_position INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_voice_entries_project
                    ON voice_entries(project_id);
                CREATE INDEX IF NOT EXISTS idx_voice_entries_project_sequence
                    ON voice_entries(project_id, sequence_id, sequence_position);

                CREATE TABLE IF NOT EXISTS voice_profiles (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    sample_path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_voice_profiles_project
                    ON voice_profiles(project_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS transcripts (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    audio_path TEXT NOT NULL,
                    transcript TEXT NOT NULL,
                    segments TEXT NOT NULL,
                    language_detected TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_transcripts_project_created
                    ON transcripts(project_id, created_at DESC);
                """
            )
            conn.commit()

    def _next_sequence_position(self, sequence_id: str) -> int:
        row = self._fetchone(
            """
            SELECT COALESCE(MAX(sequence_position), 0) AS max_pos
            FROM voice_entries
            WHERE project_id = ? AND sequence_id = ?
            """,
            (self.project_id, sequence_id),
        )
        max_pos = int(row["max_pos"]) if row is not None else 0
        return max_pos + 1

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path.as_posix(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _execute(self, query: str, params: tuple[Any, ...]) -> None:
        with self._connect() as conn:
            conn.execute(query, params)
            conn.commit()

    def _fetchone(self, query: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
        with self._connect() as conn:
            cur = conn.execute(query, params)
            return cur.fetchone()

    def _fetchall(self, query: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
        with self._connect() as conn:
            cur = conn.execute(query, params)
            return list(cur.fetchall())


def _resolve_db_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_segments(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, Mapping):
            out.append({str(k): v for k, v in item.items()})
    return out


def _to_non_negative_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
        if parsed < 0:
            return float(default)
        return parsed
    except Exception:
        return float(default)


def _loads_json(raw: Any, *, fallback: Any) -> Any:
    if raw is None:
        return fallback
    try:
        return json.loads(str(raw))
    except Exception:
        return fallback


def _sha256_file(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 128), b""):
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
