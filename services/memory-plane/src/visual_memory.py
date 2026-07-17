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
class VisualEntry:
    id: str
    project_id: str
    domain: str
    file_path: str
    prompt: str
    style_profile: str
    sequence_id: str
    sequence_position: int
    consistency_metadata: dict[str, Any]
    hardware_used: str
    duration_seconds: float
    created_at: str


class VisualMemory:
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
    def visuals_root(self) -> Path:
        return self._storage_path / safe_segment(self.project_id) / "visuals"

    def save(
        self,
        result: Mapping[str, Any],
        tags: Mapping[str, Any] | None,
        sequence_id: str | None = None,
    ) -> str:
        if not isinstance(result, Mapping):
            raise ValueError("result must be a mapping")

        seq = str(sequence_id or result.get("sequence_id") or "standalone").strip()
        if not seq:
            seq = "standalone"
        seq_safe = safe_segment(seq)

        source_path = Path(str(result.get("file_path") or "").strip()).expanduser()
        if not source_path.exists():
            raise FileNotFoundError(f"visual source file not found: {source_path}")

        prompt = str(result.get("prompt") or "").strip()
        style_profile = str(result.get("style_profile") or "safe").strip().lower() or "safe"
        domain = str((tags or {}).get("domain") or result.get("domain") or "visual").strip() or "visual"
        hardware_used = str(result.get("hardware_used") or "unknown").strip() or "unknown"
        duration_seconds = _to_non_negative_float(result.get("duration_seconds"), default=0.0)

        with self._lock:
            next_position = self._next_sequence_position(seq_safe)
            target_dir = self.visuals_root / seq_safe
            target_dir.mkdir(parents=True, exist_ok=True)
            ext = source_path.suffix.lower() or ".png"
            target_name = f"{next_position:03d}{ext}"
            target_path = (target_dir / target_name).resolve()

            if source_path.resolve() != target_path:
                shutil.copy2(source_path, target_path)

            visual_id = f"visual-{uuid4().hex[:12]}"
            created_at = _utc_now_iso()
            consistency_metadata = _normalize_consistency(result.get("consistency_metadata"))
            self._execute(
                """
                INSERT INTO visual_entries (
                    id,
                    project_id,
                    domain,
                    file_path,
                    prompt,
                    style_profile,
                    sequence_id,
                    sequence_position,
                    consistency_metadata,
                    hardware_used,
                    duration_seconds,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    visual_id,
                    self.project_id,
                    domain,
                    target_path.as_posix(),
                    prompt,
                    style_profile,
                    seq_safe,
                    int(next_position),
                    json.dumps(consistency_metadata, ensure_ascii=True, sort_keys=True),
                    hardware_used,
                    float(duration_seconds),
                    created_at,
                ),
            )

            fingerprint = _sha256_file(target_path)
            from verify.artifacts.registry import ArtifactRegistry
            registry = ArtifactRegistry(manifest_path=self._artifact_root / "registry" / "manifest.jsonl")
            registry.register(
                artifact_type="visual_asset",
                path=target_path,
                fingerprint=fingerprint,
                metadata={
                    "project_id": self.project_id,
                    "domain": domain,
                    "sequence_id": seq_safe,
                    "sequence_position": int(next_position),
                    "style_profile": style_profile,
                    "tags": dict(tags or {}),
                },
            )

            return visual_id

    def get(self, visual_id: str) -> VisualEntry:
        clean_id = str(visual_id or "").strip()
        if not clean_id:
            raise ValueError("visual_id must be non-empty")
        row = self._fetchone(
            """
            SELECT
                id,
                project_id,
                domain,
                file_path,
                prompt,
                style_profile,
                sequence_id,
                sequence_position,
                consistency_metadata,
                hardware_used,
                duration_seconds,
                created_at
            FROM visual_entries
            WHERE id = ? AND project_id = ?
            """,
            (clean_id, self.project_id),
        )
        if row is None:
            raise ValueError(f"visual entry not found: {clean_id}")
        return _row_to_entry(row)

    def get_sequence(self, sequence_id: str) -> list[VisualEntry]:
        clean_sequence_id = safe_segment(str(sequence_id or "").strip())
        if not clean_sequence_id:
            raise ValueError("sequence_id must be non-empty")
        rows = self._fetchall(
            """
            SELECT
                id,
                project_id,
                domain,
                file_path,
                prompt,
                style_profile,
                sequence_id,
                sequence_position,
                consistency_metadata,
                hardware_used,
                duration_seconds,
                created_at
            FROM visual_entries
            WHERE project_id = ? AND sequence_id = ?
            ORDER BY sequence_position ASC, created_at ASC, id ASC
            """,
            (self.project_id, clean_sequence_id),
        )
        return [_row_to_entry(row) for row in rows]

    def get_last(self, project_id: str, domain: str, n: int = 1) -> list[VisualEntry]:
        clean_project_id = str(project_id or "").strip()
        if clean_project_id != self.project_id:
            raise ValueError("project_id mismatch for VisualMemory instance")
        clean_domain = str(domain or "").strip()
        if not clean_domain:
            raise ValueError("domain must be non-empty")
        limit = max(1, int(n or 1))
        rows = self._fetchall(
            """
            SELECT
                id,
                project_id,
                domain,
                file_path,
                prompt,
                style_profile,
                sequence_id,
                sequence_position,
                consistency_metadata,
                hardware_used,
                duration_seconds,
                created_at
            FROM visual_entries
            WHERE project_id = ? AND domain = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (self.project_id, clean_domain, int(limit)),
        )
        return [_row_to_entry(row) for row in rows]

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS visual_entries (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    style_profile TEXT NOT NULL,
                    sequence_id TEXT NOT NULL,
                    sequence_position INTEGER NOT NULL,
                    consistency_metadata TEXT NOT NULL,
                    hardware_used TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_visual_entries_project
                    ON visual_entries(project_id);

                CREATE INDEX IF NOT EXISTS idx_visual_entries_project_sequence
                    ON visual_entries(project_id, sequence_id, sequence_position);

                CREATE INDEX IF NOT EXISTS idx_visual_entries_project_domain_created
                    ON visual_entries(project_id, domain, created_at DESC);
                """
            )
            conn.commit()

    def _next_sequence_position(self, sequence_id: str) -> int:
        row = self._fetchone(
            """
            SELECT COALESCE(MAX(sequence_position), 0) AS max_pos
            FROM visual_entries
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


def _to_non_negative_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
        if parsed < 0:
            return float(default)
        return parsed
    except Exception:
        return float(default)


def _normalize_consistency(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return {}


def _row_to_entry(row: sqlite3.Row) -> VisualEntry:
    consistency = _loads_json(row["consistency_metadata"], fallback={})
    if not isinstance(consistency, dict):
        consistency = {}
    return VisualEntry(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        domain=str(row["domain"]),
        file_path=str(row["file_path"]),
        prompt=str(row["prompt"]),
        style_profile=str(row["style_profile"]),
        sequence_id=str(row["sequence_id"]),
        sequence_position=int(row["sequence_position"]),
        consistency_metadata=consistency,
        hardware_used=str(row["hardware_used"]),
        duration_seconds=float(row["duration_seconds"]),
        created_at=str(row["created_at"]),
    )


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
