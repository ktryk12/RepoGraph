"""PostgreSQL context store implementation for context-plane service"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import IntegrityError

from aesa.application.ports.context_store import ContextStorePort


@dataclass(frozen=True)
class ContextEntry:
    doc_id: str
    doc_version: str
    content: str
    metadata: Dict[str, Any]
    module_layer: str
    summary: str
    exports: List[str]
    internal_deps: List[str]
    checksum: str
    ingested_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "doc_version": self.doc_version,
            "content": self.content,
            "metadata": dict(self.metadata),
            "module_layer": self.module_layer,
            "summary": self.summary,
            "exports": list(self.exports),
            "internal_deps": list(self.internal_deps),
            "checksum": self.checksum,
            "ingested_at": self.ingested_at,
        }


class PostgreSQLContextStoreAdapter(ContextStorePort):
    """
    PostgreSQL context store for Context Plane state + agent index records.

    Replaces SQLiteContextStorePortAdapter with PostgreSQL backend.
    """

    def __init__(self, *, database_url: str | None = None) -> None:
        self._database_url = database_url or self._get_default_database_url()
        self._engine = create_engine(self._database_url, pool_pre_ping=True)
        self._session_factory = sessionmaker(bind=self._engine)

    def _get_default_database_url(self) -> str:
        """Get database URL from environment variables"""
        # In production, use DATABASE_URL_CONTEXT_PLANE
        db_url = os.getenv("DATABASE_URL_CONTEXT_PLANE")
        if db_url:
            return db_url

        # For development, construct from components
        user = "babyai_context_plane_user"
        password = os.getenv("POSTGRES_PASSWORD", "context_plane_secure_password_change_in_prod")
        host = os.getenv("POSTGRES_HOST", "localhost")
        port = os.getenv("POSTGRES_DECISION_PORT", "5432")
        database = "babyai_context_plane"

        return f"postgresql://{user}:{password}@{host}:{port}/{database}"

    # ContextStorePort implementation
    def load(self, context_id: str) -> Dict[str, Any] | None:
        with self._session_factory() as session:
            result = session.execute(
                text("""
                    SELECT payload_json
                    FROM context_payloads
                    WHERE context_id = :context_id
                """),
                {"context_id": str(context_id)}
            ).fetchone()

            if result is None:
                return None

            try:
                decoded = json.loads(str(result[0]))
                return dict(decoded) if isinstance(decoded, dict) else None
            except Exception:
                return None

    def save(self, context_id: str, payload: Dict[str, Any]) -> str:
        payload_json = json.dumps(dict(payload or {}), ensure_ascii=True, sort_keys=True)

        with self._session_factory() as session:
            session.execute(
                text("""
                    INSERT INTO context_payloads (context_id, payload_json)
                    VALUES (:context_id, :payload_json)
                    ON CONFLICT (context_id) DO UPDATE SET
                        payload_json = EXCLUDED.payload_json,
                        updated_at = now()
                """),
                {"context_id": str(context_id), "payload_json": payload_json}
            )
            session.commit()

        return str(context_id)

    # Agent index APIs
    def get_entry(self, *, doc_id: str, doc_version: str | None = None) -> ContextEntry | None:
        with self._session_factory() as session:
            if isinstance(doc_version, str) and doc_version.strip():
                result = session.execute(
                    text("""
                        SELECT doc_id, doc_version, content, metadata_json, module_layer,
                               summary, exports, internal_deps, checksum, ingested_at
                        FROM context_entries
                        WHERE doc_id = :doc_id AND doc_version = :doc_version
                    """),
                    {"doc_id": str(doc_id), "doc_version": str(doc_version)}
                ).fetchone()
            else:
                result = session.execute(
                    text("""
                        SELECT doc_id, doc_version, content, metadata_json, module_layer,
                               summary, exports, internal_deps, checksum, ingested_at
                        FROM context_entries
                        WHERE doc_id = :doc_id
                        ORDER BY ingested_at DESC
                        LIMIT 1
                    """),
                    {"doc_id": str(doc_id)}
                ).fetchone()

            return self._row_to_entry(result)

    def has_checksum(self, *, doc_id: str, checksum: str) -> bool:
        with self._session_factory() as session:
            result = session.execute(
                text("""
                    SELECT 1
                    FROM context_entries
                    WHERE doc_id = :doc_id AND checksum = :checksum
                    LIMIT 1
                """),
                {"doc_id": str(doc_id), "checksum": str(checksum)}
            ).fetchone()

            return result is not None

    def upsert_entry(
        self,
        *,
        doc_id: str,
        doc_version: str,
        content: str,
        metadata: Mapping[str, Any] | None = None,
        module_layer: str = "other",
        summary: str | None = None,
        exports: Sequence[str] | None = None,
        internal_deps: Sequence[str] | None = None,
        checksum: str,
    ) -> None:
        with self._session_factory() as session:
            session.execute(
                text("""
                    INSERT INTO context_entries (
                        doc_id, doc_version, content, metadata_json, module_layer, summary,
                        exports, internal_deps, checksum
                    )
                    VALUES (:doc_id, :doc_version, :content, :metadata_json, :module_layer,
                            :summary, :exports, :internal_deps, :checksum)
                    ON CONFLICT (doc_id, doc_version) DO UPDATE SET
                        content = EXCLUDED.content,
                        metadata_json = EXCLUDED.metadata_json,
                        module_layer = EXCLUDED.module_layer,
                        summary = EXCLUDED.summary,
                        exports = EXCLUDED.exports,
                        internal_deps = EXCLUDED.internal_deps,
                        checksum = EXCLUDED.checksum
                """),
                {
                    "doc_id": str(doc_id),
                    "doc_version": str(doc_version),
                    "content": str(content),
                    "metadata_json": self._to_json(metadata or {}),
                    "module_layer": str(module_layer or "other"),
                    "summary": str(summary or ""),
                    "exports": self._to_json(list(exports or [])),
                    "internal_deps": self._to_json(list(internal_deps or [])),
                    "checksum": str(checksum),
                }
            )
            session.commit()

    def replace_dep_edges(self, *, from_doc_id: str, edges: Sequence[tuple[str, str]]) -> None:
        with self._session_factory() as session:
            # Delete existing edges
            session.execute(
                text("DELETE FROM dep_graph WHERE from_doc_id = :from_doc_id"),
                {"from_doc_id": str(from_doc_id)}
            )

            # Insert new edges
            for to_doc_id, dep_type in edges:
                if str(to_doc_id).strip() and str(dep_type).strip():
                    session.execute(
                        text("""
                            INSERT INTO dep_graph (from_doc_id, to_doc_id, dep_type)
                            VALUES (:from_doc_id, :to_doc_id, :dep_type)
                            ON CONFLICT (from_doc_id, to_doc_id, dep_type) DO NOTHING
                        """),
                        {
                            "from_doc_id": str(from_doc_id),
                            "to_doc_id": str(to_doc_id),
                            "dep_type": str(dep_type)
                        }
                    )

            session.commit()

    def list_entries(self, *, limit: int = 500) -> List[ContextEntry]:
        with self._session_factory() as session:
            results = session.execute(
                text("""
                    SELECT doc_id, doc_version, content, metadata_json, module_layer,
                           summary, exports, internal_deps, checksum, ingested_at
                    FROM context_entries
                    ORDER BY ingested_at DESC
                    LIMIT :limit
                """),
                {"limit": max(1, int(limit))}
            ).fetchall()

            return [
                entry for entry in (self._row_to_entry(row) for row in results)
                if entry is not None
            ]

    def search_entries(
        self,
        *,
        layers: Sequence[str] | None = None,
        keywords: Sequence[str] | None = None,
        focus_doc_id: str | None = None,
        include_tests: bool = True,
        limit: int = 200,
    ) -> List[ContextEntry]:
        clauses: List[str] = []
        params: Dict[str, Any] = {}

        layer_values = [str(item).strip() for item in list(layers or []) if str(item).strip()]
        if layer_values:
            placeholders = ", ".join(f":layer_{i}" for i in range(len(layer_values)))
            clauses.append(f"module_layer IN ({placeholders})")
            for i, layer in enumerate(layer_values):
                params[f"layer_{i}"] = layer

        if not include_tests:
            clauses.append("module_layer != 'test'")
            clauses.append("doc_id NOT LIKE '%test%'")

        text_terms = [str(item).strip() for item in list(keywords or []) if str(item).strip()]
        for i, term in enumerate(text_terms):
            clauses.append("(doc_id LIKE :term_{0} OR summary LIKE :term_{0} OR content LIKE :term_{0})".format(i))
            params[f"term_{i}"] = f"%{term}%"

        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params["limit"] = max(1, int(limit))

        with self._session_factory() as session:
            results = session.execute(
                text(f"""
                    SELECT doc_id, doc_version, content, metadata_json, module_layer,
                           summary, exports, internal_deps, checksum, ingested_at
                    FROM context_entries
                    {where_clause}
                    ORDER BY ingested_at DESC
                    LIMIT :limit
                """),
                params
            ).fetchall()

            entries = [
                entry for entry in (self._row_to_entry(row) for row in results)
                if entry is not None
            ]

            if not focus_doc_id:
                return entries

            focus = str(focus_doc_id).strip()
            if not focus:
                return entries

            # Sort by relevance to focus doc_id
            ranked = sorted(
                entries,
                key=lambda item: (
                    0 if item.doc_id == focus else (1 if focus in item.doc_id else 2),
                    item.doc_id,
                ),
            )
            return ranked

    def dependency_summary(self, *, doc_ids: Sequence[str], max_rows: int = 80) -> str:
        clean_doc_ids = [str(item).strip() for item in doc_ids if str(item).strip()]
        if not clean_doc_ids:
            return ""

        params = {"max_rows": max(1, int(max_rows))}
        placeholders = ", ".join(f":doc_id_{i}" for i in range(len(clean_doc_ids)))
        for i, doc_id in enumerate(clean_doc_ids):
            params[f"doc_id_{i}"] = doc_id

        with self._session_factory() as session:
            results = session.execute(
                text(f"""
                    SELECT from_doc_id, to_doc_id, dep_type
                    FROM dep_graph
                    WHERE from_doc_id IN ({placeholders})
                    ORDER BY from_doc_id, dep_type, to_doc_id
                    LIMIT :max_rows
                """),
                params
            ).fetchall()

            if not results:
                return ""

            lines = [f"{row[0]} -[{row[2]}]-> {row[1]}" for row in results]
            return "\n".join(lines)

    def count_entries(self) -> int:
        with self._session_factory() as session:
            result = session.execute(text("SELECT COUNT(*) FROM context_entries")).fetchone()
            return int(result[0] or 0) if result else 0

    def latest_ingested_at(self) -> str | None:
        with self._session_factory() as session:
            result = session.execute(text("SELECT MAX(ingested_at) FROM context_entries")).fetchone()
            if result and result[0]:
                return str(result[0])
            return None

    # Retrieval history
    def record_retrieval(
        self,
        *,
        task_description: str,
        task_type: str,
        doc_ids_retrieved: Sequence[str],
        strategy_used: str,
        consumer: str,
    ) -> int:
        payload = self._to_json([str(item) for item in doc_ids_retrieved if str(item).strip()])

        with self._session_factory() as session:
            result = session.execute(
                text("""
                    INSERT INTO context_retrievals (
                        task_description, task_type, doc_ids_retrieved, strategy_used, was_useful, consumer
                    )
                    VALUES (:task_description, :task_type, :doc_ids_retrieved, :strategy_used, NULL, :consumer)
                    RETURNING id
                """),
                {
                    "task_description": str(task_description),
                    "task_type": str(task_type),
                    "doc_ids_retrieved": payload,
                    "strategy_used": str(strategy_used),
                    "consumer": str(consumer),
                }
            )
            session.commit()
            return int(result.fetchone()[0])

    def set_retrieval_feedback(self, *, retrieval_id: int, was_useful: bool) -> bool:
        with self._session_factory() as session:
            result = session.execute(
                text("""
                    UPDATE context_retrievals
                    SET was_useful = :was_useful
                    WHERE id = :retrieval_id
                """),
                {"was_useful": 1 if bool(was_useful) else 0, "retrieval_id": int(retrieval_id)}
            )
            session.commit()
            return result.rowcount > 0

    def recent_retrievals(self, *, task_type: str, limit: int = 10) -> List[Dict[str, Any]]:
        with self._session_factory() as session:
            results = session.execute(
                text("""
                    SELECT id, task_description, task_type, doc_ids_retrieved, strategy_used,
                           was_useful, consumer, created_at
                    FROM context_retrievals
                    WHERE task_type = :task_type
                    ORDER BY created_at DESC, id DESC
                    LIMIT :limit
                """),
                {"task_type": str(task_type), "limit": max(1, int(limit))}
            ).fetchall()

            out: List[Dict[str, Any]] = []
            for row in results:
                out.append({
                    "id": int(row[0]),
                    "task_description": str(row[1] or ""),
                    "task_type": str(row[2] or ""),
                    "doc_ids_retrieved": self._from_json_list(row[3]),
                    "strategy_used": str(row[4] or ""),
                    "was_useful": None if row[5] is None else bool(int(row[5])),
                    "consumer": str(row[6] or ""),
                    "created_at": str(row[7] or ""),
                })
            return out

    def _row_to_entry(self, row: Any) -> ContextEntry | None:
        if row is None:
            return None

        return ContextEntry(
            doc_id=str(row[0] or ""),
            doc_version=str(row[1] or ""),
            content=str(row[2] or ""),
            metadata=self._from_json_dict(row[3]),
            module_layer=str(row[4] or "other"),
            summary=str(row[5] or ""),
            exports=self._from_json_list(row[6]),
            internal_deps=self._from_json_list(row[7]),
            checksum=str(row[8] or ""),
            ingested_at=str(row[9] or ""),
        )

    def _to_json(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)

    def _from_json_dict(self, raw: Any) -> Dict[str, Any]:
        try:
            decoded = json.loads(str(raw or "{}"))
        except Exception:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}

    def _from_json_list(self, raw: Any) -> List[str]:
        try:
            decoded = json.loads(str(raw or "[]"))
        except Exception:
            return []
        if not isinstance(decoded, list):
            return []
        return [str(item) for item in decoded if str(item).strip()]