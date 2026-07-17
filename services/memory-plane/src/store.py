from __future__ import annotations

import json
import math
import pickle
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class MemoryStore:
    """Persistent local memory store backed by SQLite with WAL mode.

    Two tables:
      - memories: content + metadata
      - embeddings: float vectors (pickled) for cosine similarity search
    """

    DEFAULT_DB_PATH = "/data/memory_plane/memories.db"

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                content     TEXT NOT NULL,
                source      TEXT NOT NULL,
                entity_type TEXT,
                entity_id   TEXT,
                metadata    TEXT,
                created_at  REAL NOT NULL,
                importance  REAL DEFAULT 0.5
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                memory_id   INTEGER PRIMARY KEY,
                vector      BLOB NOT NULL,
                dim         INTEGER NOT NULL,
                FOREIGN KEY (memory_id) REFERENCES memories(id)
            )
        """)
        self._conn.commit()

    def save(
        self,
        content: str,
        source: str,
        entity_type: str = "",
        entity_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
        importance: float = 0.5,
    ) -> int:
        """Save a memory and optionally its embedding. Returns memory_id."""
        meta_json = json.dumps(metadata or {})
        now = time.time()
        with self._conn:
            cursor = self._conn.execute(
                """INSERT INTO memories
                   (content, source, entity_type, entity_id, metadata, created_at, importance)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (content, source, entity_type or "", entity_id or "", meta_json, now, float(importance)),
            )
            memory_id = cursor.lastrowid
            if embedding:
                blob = pickle.dumps(embedding)
                self._conn.execute(
                    "INSERT INTO embeddings (memory_id, vector, dim) VALUES (?, ?, ?)",
                    (memory_id, blob, len(embedding)),
                )
        return memory_id

    def search(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        entity_type: Optional[str] = None,
        min_importance: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """Cosine similarity search over stored embeddings.

        Returns top_k results sorted by descending similarity.
        Only memories with stored embeddings and importance >= min_importance
        are considered.
        """
        if not query_embedding:
            return []

        conditions = ["m.importance >= ?"]
        params: List[Any] = [min_importance]
        if entity_type:
            conditions.append("m.entity_type = ?")
            params.append(entity_type)
        where = " AND ".join(conditions)

        rows = self._conn.execute(
            f"""SELECT m.id, m.content, m.entity_type, m.entity_id,
                       m.metadata, m.created_at, m.importance, e.vector
                FROM memories m
                JOIN embeddings e ON e.memory_id = m.id
                WHERE {where}""",
            params,
        ).fetchall()

        scored: List[tuple] = []
        for row in rows:
            id_, content, etype, eid, meta_json, created_at, imp, blob = row
            try:
                vec: List[float] = pickle.loads(blob)
                sim = self.cosine_similarity(query_embedding, vec)
            except Exception:
                continue
            scored.append((sim, {
                "id": id_,
                "content": content,
                "entity_type": etype,
                "entity_id": eid,
                "metadata": json.loads(meta_json) if meta_json else {},
                "similarity": sim,
                "created_at": created_at,
                "importance": imp,
            }))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored[:top_k]]

    def get_by_entity(self, entity_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch the most recent memories for a specific entity."""
        rows = self._conn.execute(
            """SELECT id, content, entity_type, entity_id, metadata, created_at, importance
               FROM memories
               WHERE entity_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (entity_id, limit),
        ).fetchall()
        results = []
        for row in rows:
            id_, content, etype, eid, meta_json, created_at, imp = row
            results.append({
                "id": id_,
                "content": content,
                "entity_type": etype,
                "entity_id": eid,
                "metadata": json.loads(meta_json) if meta_json else {},
                "created_at": created_at,
                "importance": imp,
            })
        return results

    @staticmethod
    def cosine_similarity(a: List[float], b: List[float]) -> float:
        """Pure Python cosine similarity — no numpy dependency."""
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0.0 or mag_b == 0.0:
            return 0.0
        return dot / (mag_a * mag_b)
