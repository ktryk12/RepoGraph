from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import RLock
from typing import Any
from uuid import uuid4


_ENTRY_TYPES = ("event", "working", "knowledge")
_TTL_24H_SECONDS = 24 * 60 * 60
_ACTIVE_EPISODE_STATUSES = {"active", "running", "in_progress"}


class VirtualMemory:
    def __init__(
        self,
        *,
        db_path: str | Path = "state/babyai_memory.sqlite",
        redis_client: Any | None = None,
    ) -> None:
        self._db_path = initialize_memory_schema(db_path)
        self._lock = RLock()
        self._redis = redis_client if redis_client is not None else _InMemoryRedis()

    @property
    def db_path(self) -> Path:
        return Path(self._db_path)

    @property
    def redis(self) -> Any:
        return self._redis

    def load(self, project_id: str, domain: str) -> dict[str, Any]:
        clean_project_id = _normalize_id(project_id, field_name="project_id")
        clean_domain = _normalize_domain(domain)
        project_row = self._fetchone(
            """
            SELECT id, name, domains, created_at, last_active
            FROM projects
            WHERE id = ?
            """,
            (clean_project_id,),
        )
        if project_row is None:
            raise ValueError(f"project not found: {clean_project_id}")

        entry_rows = self._fetchall(
            """
            SELECT id, type, content, created_at
            FROM memory_entries
            WHERE project_id = ? AND domain = ?
            ORDER BY created_at ASC, id ASC
            """,
            (clean_project_id, clean_domain),
        )
        episode_rows = self._fetchall(
            """
            SELECT id, status, turns, result, created_at
            FROM episodes
            WHERE project_id = ? AND domain = ?
            ORDER BY created_at DESC, id DESC
            """,
            (clean_project_id, clean_domain),
        )

        memory = {"event": [], "working": [], "knowledge": []}
        for row in entry_rows:
            item = {
                "id": str(row["id"]),
                "type": str(row["type"]),
                "content": _loads_json(row["content"], fallback={}),
                "created_at": str(row["created_at"]),
            }
            memory[item["type"]].append(item)

        episodes: list[dict[str, Any]] = []
        for row in episode_rows:
            turns = _loads_json(row["turns"], fallback=[])
            item = {
                "id": str(row["id"]),
                "status": str(row["status"]),
                "turns": turns if isinstance(turns, list) else [],
                "result": _loads_json(row["result"], fallback=str(row["result"] or "")),
                "created_at": str(row["created_at"]),
            }
            episodes.append(item)
            if item["status"].lower() in _ACTIVE_EPISODE_STATUSES:
                self._cache_episode_context(clean_project_id, item["id"], item["turns"])
                self._redis_sadd(_active_episodes_key(clean_project_id), item["id"])

        return {
            "project_id": str(project_row["id"]),
            "project_name": str(project_row["name"]),
            "domain": clean_domain,
            "domains": _loads_json(project_row["domains"], fallback=[]),
            "created_at": str(project_row["created_at"]),
            "last_active": str(project_row["last_active"]),
            "memory": memory,
            "episodes": episodes,
        }

    def save(self, project_id: str, domain: str, entry_type: str, content: Any) -> None:
        clean_project_id = _normalize_id(project_id, field_name="project_id")
        clean_domain = _normalize_domain(domain)
        clean_entry_type = _normalize_entry_type(entry_type)
        self._ensure_project_exists(clean_project_id)

        now = _utc_now_iso()
        entry_id = str(uuid4())
        encoded_content = _dumps_json(content)
        self._execute(
            """
            INSERT INTO memory_entries (id, project_id, domain, type, content, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (entry_id, clean_project_id, clean_domain, clean_entry_type, encoded_content, now),
        )
        self._touch_project(clean_project_id)

        self._redis_rpush(
            _domain_memory_key(clean_project_id, clean_domain),
            _dumps_json(
                {
                    "id": entry_id,
                    "project_id": clean_project_id,
                    "domain": clean_domain,
                    "type": clean_entry_type,
                    "content": content,
                    "created_at": now,
                }
            ),
        )
        self._materialize_episode_from_content(
            project_id=clean_project_id,
            domain=clean_domain,
            content=content,
            created_at=now,
        )

    def get_context(self, project_id: str, domain: str, n: int = 10) -> list[dict[str, Any]]:
        clean_project_id = _normalize_id(project_id, field_name="project_id")
        clean_domain = _normalize_domain(domain)
        self._ensure_project_exists(clean_project_id)
        limit = max(1, int(n))
        rows = self._fetchall(
            """
            SELECT id, type, content, created_at
            FROM memory_entries
            WHERE project_id = ? AND domain = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (clean_project_id, clean_domain, limit),
        )
        rows.reverse()
        return [
            {
                "id": str(row["id"]),
                "type": str(row["type"]),
                "content": _loads_json(row["content"], fallback={}),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def clear(self, project_id: str, domain: str, layer: str) -> None:
        clean_project_id = _normalize_id(project_id, field_name="project_id")
        clean_domain = _normalize_domain(domain)
        clean_layer = str(layer or "").strip().lower()
        self._ensure_project_exists(clean_project_id)

        if clean_layer == "working":
            types = ("working",)
        elif clean_layer == "knowledge":
            types = ("knowledge",)
        elif clean_layer == "all":
            types = _ENTRY_TYPES
        else:
            raise ValueError("layer must be one of: working, knowledge, all")

        placeholders = ",".join("?" for _ in types)
        params = (clean_project_id, clean_domain, *types)
        self._execute(
            f"""
            DELETE FROM memory_entries
            WHERE project_id = ? AND domain = ? AND type IN ({placeholders})
            """,
            params,
        )
        self._touch_project(clean_project_id)

        memory_key = _domain_memory_key(clean_project_id, clean_domain)
        self._redis_delete(memory_key)
        if clean_layer != "all":
            remaining_rows = self._fetchall(
                """
                SELECT id, type, content, created_at
                FROM memory_entries
                WHERE project_id = ? AND domain = ?
                ORDER BY created_at ASC, id ASC
                """,
                (clean_project_id, clean_domain),
            )
            for row in remaining_rows:
                self._redis_rpush(
                    memory_key,
                    _dumps_json(
                        {
                            "id": str(row["id"]),
                            "project_id": clean_project_id,
                            "domain": clean_domain,
                            "type": str(row["type"]),
                            "content": _loads_json(row["content"], fallback={}),
                            "created_at": str(row["created_at"]),
                        }
                    ),
                )

        if clean_layer == "all":
            episode_rows = self._fetchall(
                """
                SELECT id
                FROM episodes
                WHERE project_id = ? AND domain = ?
                """,
                (clean_project_id, clean_domain),
            )
            active_key = _active_episodes_key(clean_project_id)
            for row in episode_rows:
                episode_id = str(row["id"])
                self._redis_delete(_episode_context_key(clean_project_id, episode_id))
                self._redis_srem(active_key, episode_id)

    def snapshot(self, project_id: str) -> dict[str, Any]:
        clean_project_id = _normalize_id(project_id, field_name="project_id")
        project_row = self._fetchone(
            """
            SELECT id, name, domains, created_at, last_active
            FROM projects
            WHERE id = ?
            """,
            (clean_project_id,),
        )
        if project_row is None:
            raise ValueError(f"project not found: {clean_project_id}")

        memory_rows = self._fetchall(
            """
            SELECT domain, type, COUNT(*) AS c
            FROM memory_entries
            WHERE project_id = ?
            GROUP BY domain, type
            ORDER BY domain ASC, type ASC
            """,
            (clean_project_id,),
        )
        counts: dict[str, dict[str, int]] = {}
        for row in memory_rows:
            domain = str(row["domain"])
            domain_counts = counts.setdefault(domain, {"event": 0, "working": 0, "knowledge": 0})
            domain_counts[str(row["type"])] = int(row["c"] or 0)

        episode_rows = self._fetchall(
            """
            SELECT domain, status, COUNT(*) AS c
            FROM episodes
            WHERE project_id = ?
            GROUP BY domain, status
            ORDER BY domain ASC, status ASC
            """,
            (clean_project_id,),
        )
        episode_by_domain: dict[str, dict[str, int]] = {}
        total_episodes = 0
        total_active = 0
        for row in episode_rows:
            domain = str(row["domain"])
            status = str(row["status"]).lower()
            count = int(row["c"] or 0)
            total_episodes += count
            if status in _ACTIVE_EPISODE_STATUSES:
                total_active += count
            domain_status = episode_by_domain.setdefault(domain, {})
            domain_status[status] = count

        return {
            "project": {
                "id": str(project_row["id"]),
                "name": str(project_row["name"]),
                "domains": _loads_json(project_row["domains"], fallback=[]),
                "created_at": str(project_row["created_at"]),
                "last_active": str(project_row["last_active"]),
            },
            "memory_counts": counts,
            "episode_summary": {
                "total": total_episodes,
                "active": total_active,
                "by_domain": episode_by_domain,
            },
            "generated_at": _utc_now_iso(),
        }

    def import_from(self, source_project_id: str, domain: str, target_project_id: str) -> int:
        clean_source_id = _normalize_id(source_project_id, field_name="source_project_id")
        clean_target_id = _normalize_id(target_project_id, field_name="target_project_id")
        clean_domain = _normalize_domain(domain)
        self._ensure_project_exists(clean_source_id)
        self._ensure_project_exists(clean_target_id)

        rows = self._fetchall(
            """
            SELECT type, content
            FROM memory_entries
            WHERE project_id = ? AND domain = ?
            ORDER BY created_at ASC, id ASC
            """,
            (clean_source_id, clean_domain),
        )
        inserted = 0
        for row in rows:
            content = _loads_json(row["content"], fallback={})
            self.save(
                project_id=clean_target_id,
                domain=clean_domain,
                entry_type=str(row["type"]),
                content=content,
            )
            inserted += 1
        return inserted

    def _materialize_episode_from_content(
        self,
        *,
        project_id: str,
        domain: str,
        content: Any,
        created_at: str,
    ) -> None:
        if not isinstance(content, Mapping):
            return
        raw_episode_id = content.get("episode_id")
        if raw_episode_id is None:
            return
        episode_id = str(raw_episode_id).strip()
        if not episode_id:
            return

        raw_turns = content.get("turns", [])
        turns = raw_turns if isinstance(raw_turns, list) else []
        status = str(content.get("status", "active") or "active").strip().lower()
        if not status:
            status = "active"
        raw_result = content.get("result")
        result_payload = None if raw_result is None else _dumps_json(raw_result)
        self._execute(
            """
            INSERT INTO episodes (id, project_id, domain, status, turns, result, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                project_id = excluded.project_id,
                domain = excluded.domain,
                status = excluded.status,
                turns = excluded.turns,
                result = excluded.result
            """,
            (
                episode_id,
                project_id,
                domain,
                status,
                _dumps_json(turns),
                result_payload,
                created_at,
            ),
        )
        self._cache_episode_context(project_id, episode_id, turns)
        active_key = _active_episodes_key(project_id)
        if status in _ACTIVE_EPISODE_STATUSES:
            self._redis_sadd(active_key, episode_id)
        else:
            self._redis_srem(active_key, episode_id)

    def _cache_episode_context(self, project_id: str, episode_id: str, turns: Any) -> None:
        payload = _dumps_json(turns if isinstance(turns, list) else [])
        self._redis_setex(_episode_context_key(project_id, episode_id), _TTL_24H_SECONDS, payload)

    def _ensure_project_exists(self, project_id: str) -> None:
        row = self._fetchone("SELECT id FROM projects WHERE id = ?", (project_id,))
        if row is None:
            raise ValueError(f"project not found: {project_id}")

    def _touch_project(self, project_id: str) -> None:
        self._execute(
            """
            UPDATE projects
            SET last_active = ?
            WHERE id = ?
            """,
            (_utc_now_iso(), project_id),
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path.as_posix(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _execute(self, query: str, params: tuple[Any, ...]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(query, params)
            conn.commit()

    def _fetchone(self, query: str, params: tuple[Any, ...]) -> sqlite3.Row | None:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(query, params)
            return cursor.fetchone()

    def _fetchall(self, query: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(query, params)
            return list(cursor.fetchall())

    def _redis_setex(self, key: str, ttl_seconds: int, value: str) -> None:
        method = getattr(self._redis, "setex", None)
        if callable(method):
            method(str(key), int(ttl_seconds), str(value))

    def _redis_sadd(self, key: str, value: str) -> None:
        method = getattr(self._redis, "sadd", None)
        if callable(method):
            method(str(key), str(value))

    def _redis_srem(self, key: str, value: str) -> None:
        method = getattr(self._redis, "srem", None)
        if callable(method):
            method(str(key), str(value))

    def _redis_rpush(self, key: str, value: str) -> None:
        method = getattr(self._redis, "rpush", None)
        if callable(method):
            method(str(key), str(value))

    def _redis_delete(self, key: str) -> None:
        method = getattr(self._redis, "delete", None)
        if callable(method):
            method(str(key))


def initialize_memory_schema(db_path: str | Path) -> Path:
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    schema_path = Path(__file__).with_name("schema.sql")
    script = schema_path.read_text(encoding="utf-8")
    with sqlite3.connect(path.as_posix()) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(script)
        conn.commit()
    return path


class _InMemoryRedis:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._sets: dict[str, set[str]] = {}
        self._lists: dict[str, list[str]] = {}
        self._ttls: dict[str, int] = {}

    def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        self._values[str(key)] = str(value)
        self._ttls[str(key)] = int(ttl_seconds)

    def sadd(self, key: str, *values: str) -> None:
        row = self._sets.setdefault(str(key), set())
        for value in values:
            row.add(str(value))

    def srem(self, key: str, *values: str) -> None:
        row = self._sets.get(str(key))
        if row is None:
            return
        for value in values:
            row.discard(str(value))
        if not row:
            self._sets.pop(str(key), None)

    def smembers(self, key: str) -> set[str]:
        return set(self._sets.get(str(key), set()))

    def rpush(self, key: str, *values: str) -> int:
        row = self._lists.setdefault(str(key), [])
        for value in values:
            row.append(str(value))
        return len(row)

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        row = list(self._lists.get(str(key), []))
        if not row:
            return []
        start_index = int(start)
        stop_index = int(stop)
        if stop_index == -1:
            stop_index = len(row) - 1
        if start_index < 0:
            start_index = len(row) + start_index
        if stop_index < 0:
            stop_index = len(row) + stop_index
        start_index = max(0, start_index)
        stop_index = min(len(row) - 1, stop_index)
        if start_index > stop_index:
            return []
        return row[start_index : stop_index + 1]

    def delete(self, key: str) -> int:
        removed = 0
        for store in (self._values, self._sets, self._lists, self._ttls):
            if str(key) in store:
                del store[str(key)]
                removed += 1
        return removed


def _episode_context_key(project_id: str, episode_id: str) -> str:
    return f"project:{project_id}:episode:{episode_id}:context"


def _active_episodes_key(project_id: str) -> str:
    return f"project:{project_id}:active_episodes"


def _domain_memory_key(project_id: str, domain: str) -> str:
    return f"project:{project_id}:domain:{domain}:memory"


def _normalize_entry_type(entry_type: str) -> str:
    clean = str(entry_type or "").strip().lower()
    if clean not in _ENTRY_TYPES:
        allowed = ", ".join(_ENTRY_TYPES)
        raise ValueError(f"entry_type must be one of: {allowed}")
    return clean


def _normalize_id(value: str, *, field_name: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    return clean


def _normalize_domain(domain: str) -> str:
    clean = str(domain or "").strip()
    if not clean:
        raise ValueError("domain must be non-empty")
    return clean


def _dumps_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)


def _loads_json(raw: Any, *, fallback: Any) -> Any:
    if raw is None:
        return fallback
    try:
        return json.loads(str(raw))
    except Exception:
        return fallback


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
