"""PostgreSQL memory store implementation for memory-plane service"""

from __future__ import annotations

import json
import math
import pickle
import time
import os
from typing import Any, Dict, List, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError


class PostgreSQLMemoryStore:
    """Persistent memory store backed by PostgreSQL with vector embeddings.

    Two tables:
      - memories: content + metadata
      - embeddings: binary vectors (pickled) for cosine similarity search

    Maintains API compatibility with SQLite MemoryStore.
    """

    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = database_url or self._get_default_database_url()
        self._engine = create_engine(self._database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(bind=self._engine)

    def _get_default_database_url(self) -> str:
        """Get database URL from environment variables"""
        # In production, use DATABASE_URL_MEMORY_PLANE
        db_url = os.getenv("DATABASE_URL_MEMORY_PLANE")
        if db_url:
            return db_url

        # For development, construct from components
        user = "babyai_memory_plane_user"
        password = os.getenv("POSTGRES_PASSWORD", "memory_plane_secure_password_change_in_prod")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_DECISION_PORT", "5432")
        database = "babyai_memory_plane"

        return f"postgresql://{user}:{password}@{host}:{port}/{database}"

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

        with self._session_factory() as session:
            # Insert memory record
            result = session.execute(
                text("""
                    INSERT INTO memories
                    (content, source, entity_type, entity_id, metadata_json, created_at, importance)
                    VALUES (:content, :source, :entity_type, :entity_id, :metadata_json, :created_at, :importance)
                    RETURNING id
                """),
                {
                    "content": content,
                    "source": source,
                    "entity_type": entity_type or "",
                    "entity_id": entity_id or "",
                    "metadata_json": meta_json,
                    "created_at": now,
                    "importance": float(importance),
                }
            )
            memory_id = result.fetchone()[0]

            # Insert embedding if provided
            if embedding:
                blob = pickle.dumps(embedding)
                session.execute(
                    text("""
                        INSERT INTO embeddings (memory_id, vector, dim)
                        VALUES (:memory_id, :vector, :dim)
                    """),
                    {
                        "memory_id": memory_id,
                        "vector": blob,
                        "dim": len(embedding),
                    }
                )

            session.commit()
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

        params = {"min_importance": min_importance}
        conditions = ["m.importance >= :min_importance"]

        if entity_type:
            conditions.append("m.entity_type = :entity_type")
            params["entity_type"] = entity_type

        where_clause = " AND ".join(conditions)

        with self._session_factory() as session:
            results = session.execute(
                text(f"""
                    SELECT m.id, m.content, m.entity_type, m.entity_id,
                           m.metadata_json, m.created_at, m.importance, e.vector
                    FROM memories m
                    JOIN embeddings e ON e.memory_id = m.id
                    WHERE {where_clause}
                """),
                params
            ).fetchall()

            scored: List[tuple] = []
            for row in results:
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

            # Sort by similarity (descending) and return top_k
            scored.sort(key=lambda x: x[0], reverse=True)
            return [item for _, item in scored[:top_k]]

    def get_by_entity(self, entity_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Fetch the most recent memories for a specific entity."""
        with self._session_factory() as session:
            results = session.execute(
                text("""
                    SELECT id, content, entity_type, entity_id, metadata_json, created_at, importance
                    FROM memories
                    WHERE entity_id = :entity_id
                    ORDER BY created_at DESC
                    LIMIT :limit
                """),
                {"entity_id": entity_id, "limit": limit}
            ).fetchall()

            output = []
            for row in results:
                id_, content, etype, eid, meta_json, created_at, imp = row
                output.append({
                    "id": id_,
                    "content": content,
                    "entity_type": etype,
                    "entity_id": eid,
                    "metadata": json.loads(meta_json) if meta_json else {},
                    "created_at": created_at,
                    "importance": imp,
                })
            return output

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