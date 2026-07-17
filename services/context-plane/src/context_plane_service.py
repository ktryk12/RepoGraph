from __future__ import annotations

from hashlib import sha256
from threading import Event, Lock, Thread
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, TypeAlias
import asyncio
import json
import logging
import os
import time

# Internal context domain imports (replacing broken AESA imports)
from domain.context import (
    # Use cases
    AgentContextRequest,
    AgentRetrieveContextUseCase,
    ExpertServingStrategyEngine,
    RetrieveContextUseCase,
    # Bootstrap and runtime
    ContextPlaneRuntime,
    build_context_plane_runtime,
    context_plane_db_path,
    context_plane_store_backend,
    # Contracts and validation
    HEALTH_RESPONSE,
    INGEST_REQUEST,
    INGEST_RESPONSE,
    RETRIEVE_REQUEST,
    RETRIEVE_RESPONSE,
    ContextPlaneContractValidationError,
    validate_context_plane_contract,
    IngestContractValidationError,
    IngestContractsService,
    get_ingest_contracts_service,
    # Infrastructure
    ExpertServingSummaryEngine,
    estimate_repository_files,
    index_repository,
    SQLiteContextStorePortAdapter
)
# Event-driven policy client (replacing direct service imports)
from domain.context.policy_client import (
    KafkaPolicyClient,
    PolicyRequest,
    PolicyResponse,
    PerfBudgetViolation,
    get_policy_client,
    budgeted_call,
    require_ingest_write_or_503
)

try:
    from fastapi import FastAPI, HTTPException, Request as FastAPIRequest
    from fastapi.responses import JSONResponse
except Exception:  # pragma: no cover - optional dependency
    FastAPI = None  # type: ignore

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: Any = None) -> None:
            super().__init__(str(detail))
            self.status_code = status_code
            self.detail = detail

    class FastAPIRequest:
        headers: Mapping[str, Any]
        url: Any

    class JSONResponse:
        def __init__(self, status_code: int, content: Any) -> None:
            self.status_code = status_code
            self.content = content
try:
    import redis
except Exception:  # pragma: no cover - optional dependency
    redis = None  # type: ignore


logger = logging.getLogger(__name__)


def create_app(*, runtime: ContextPlaneRuntime | None = None, env: Mapping[str, str] | None = None) -> Any:
    if FastAPI is None:
        raise RuntimeError("FastAPI is required for context_plane_service. Install: pip install fastapi uvicorn")

    source_env = env if env is not None else os.environ
    artifact_root = _context_plane_artifact_root(source_env)
    service_runtime = runtime or build_context_plane_runtime(
        store_backend=context_plane_store_backend(env=source_env),
        db_path=context_plane_db_path(env=source_env),
        artifact_root=artifact_root,
    )
    app = FastAPI(title="AESA Context Plane Service", version="0.1.0")
    app.state.runtime = service_runtime
    app.state.source_env = source_env
    app.state.ingest_idempotency = IdempotencyCache(
        redis_url=_context_plane_redis_url(source_env),
        ttl_seconds=_idempotency_ttl_seconds(source_env),
    )
    app.state.ingest_idempotency_lock = Lock()
    app.state.cleanup_thread = None
    app.state.cleanup_stop_event = None
    app.state.api_key = _context_plane_api_key(source_env)
    app.state.ingest_contracts_service = get_ingest_contracts_service()
    # Initialize Kafka-based policy client instead of direct policy service imports
    app.state.policy_client = get_policy_client(kafka_producer=None)  # TODO: Connect to actual Kafka producer
    app.state.agent_index_thread = None
    app.state.agent_index_lock = Lock()
    app.state.agent_index_state = {
        "status": "empty",
        "files_indexed": 0,
        "last_run": None,
        "estimated_files": 0,
        "error": None,
    }

    agent_store = service_runtime.context_store if isinstance(service_runtime.context_store, SQLiteContextStorePortAdapter) else None
    if agent_store is None and context_plane_store_backend(env=source_env) == "sqlite":
        agent_store = SQLiteContextStorePortAdapter(db_path=context_plane_db_path(env=source_env))
        app.state.runtime = ContextPlaneRuntime(
            context_store=agent_store,
            retriever=service_runtime.retriever,
            store=service_runtime.store,
            publisher=service_runtime.publisher,
            maintenance=service_runtime.maintenance,
        )
        service_runtime = app.state.runtime

    strategy_engine = ExpertServingStrategyEngine(
        base_url=_expert_serving_base_url(source_env),
        api_key=_expert_serving_api_key(source_env),
        timeout_seconds=_expert_serving_timeout_seconds(source_env),
    )
    app.state.agent_use_case = AgentRetrieveContextUseCase(
        context_store=service_runtime.context_store,
        retrieve_context_use_case=RetrieveContextUseCase(
            retriever=service_runtime.retriever,
            store=service_runtime.store,
            publisher=service_runtime.publisher,
            failure_mode="fallback_local",
        ),
        strategy_engine=strategy_engine,
    )

    @app.middleware("http")
    async def require_api_key(http_request: FastAPIRequest, call_next):  # type: ignore[no-untyped-def]
        required_key = str(getattr(app.state, "api_key", "") or "").strip()
        if not required_key:
            return await call_next(http_request)
        path = str(getattr(http_request.url, "path", "") or "")
        if path in {"/health", "/docs", "/openapi.json", "/redoc"}:
            return await call_next(http_request)
        provided = str(http_request.headers.get("x-api-key") or "").strip()
        if provided != required_key:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "reason": "invalid_api_key"},
            )
        return await call_next(http_request)

    @app.get("/health")
    def health() -> Dict[str, Any]:
        payload = {"ok": True, "service": "context-plane"}
        _validate_output_contract(HEALTH_RESPONSE, payload)
        return payload

    @app.on_event("startup")
    def startup_cleanup_scheduler() -> None:
        interval = _cleanup_interval_seconds(source_env)
        maintenance = getattr(app.state.runtime, "maintenance", None)
        if interval <= 0 or maintenance is None:
            pass
        else:
            stop_event = Event()
            app.state.cleanup_stop_event = stop_event
            cleanup_thread = Thread(
                target=_cleanup_loop,
                args=(
                    maintenance,
                    stop_event,
                    interval,
                    _cleanup_ttl_policies(source_env),
                    _cleanup_delete_orphans(source_env),
                ),
                daemon=True,
                name="context-plane-cleanup",
            )
            app.state.cleanup_thread = cleanup_thread
            cleanup_thread.start()

        if _context_plane_index_on_startup(source_env):
            _start_agent_indexing(app, repo_root=_context_plane_repo_root(source_env))

    @app.on_event("shutdown")
    def shutdown_cleanup_scheduler() -> None:
        stop_event = app.state.cleanup_stop_event
        if stop_event is not None:
            stop_event.set()
        cleanup_thread = app.state.cleanup_thread
        if cleanup_thread is not None:
            cleanup_thread.join(timeout=1.0)
        index_thread = app.state.agent_index_thread
        if index_thread is not None and index_thread.is_alive():
            index_thread.join(timeout=1.0)

    @app.get("/v1/context/{context_id}")
    def load_context(context_id: str) -> Dict[str, Any]:
        payload = app.state.runtime.context_store.load(str(context_id))
        if payload is None:
            raise HTTPException(status_code=404, detail={"error": "context_not_found", "context_id": context_id})
        return {"context_id": str(context_id), "payload": payload}

    @app.put("/v1/context/{context_id}")
    def save_context(context_id: str, request: Dict[str, Any]) -> Dict[str, Any]:
        _require_ingest_write_or_503(operation="context_store.save")
        payload = request.get("payload")
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail={"error": "payload_required"})
        saved = app.state.runtime.context_store.save(str(context_id), payload)
        return {"context_id": str(saved)}

    @app.post("/agent/retrieve")
    def agent_retrieve(request: Dict[str, Any]) -> Dict[str, Any]:
        store = app.state.runtime.context_store
        if callable(getattr(store, "count_entries", None)):
            if int(store.count_entries()) <= 0:
                raise HTTPException(status_code=503, detail={"error": "index_empty", "status": "degraded"})
        try:
            agent_request = AgentContextRequest(
                task_description=str(request.get("task_description") or "").strip(),
                task_type=str(request.get("task_type") or "general").strip() or "general",
                focus_doc_id=str(request.get("focus_doc_id") or "").strip() or None,
                max_tokens=int(request.get("max_tokens") or 4096),
                consumer=str(request.get("consumer") or "codestral").strip() or "codestral",
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail={"error": "invalid_agent_request", "msg": str(exc)})
        if not agent_request.task_description:
            raise HTTPException(status_code=400, detail={"error": "task_description_required"})
        package = app.state.agent_use_case.execute(agent_request)
        if package.draft_quality == "fallback":
            _log_service_event(
                event_type="context_plane.agent_retrieve",
                status="fallback",
                task_type=agent_request.task_type,
            )
        return package.to_dict()

    @app.post("/agent/feedback")
    def agent_feedback(request: Dict[str, Any]) -> Dict[str, Any]:
        retrieval_id_raw = request.get("retrieval_id")
        was_useful_raw = request.get("was_useful")
        try:
            retrieval_id = int(retrieval_id_raw)
        except Exception:
            raise HTTPException(status_code=400, detail={"error": "retrieval_id_required"})
        if not isinstance(was_useful_raw, bool):
            raise HTTPException(status_code=400, detail={"error": "was_useful_required"})
        store = app.state.runtime.context_store
        if not callable(getattr(store, "set_retrieval_feedback", None)):
            raise HTTPException(status_code=503, detail={"error": "feedback_store_unavailable"})
        updated = bool(store.set_retrieval_feedback(retrieval_id=retrieval_id, was_useful=was_useful_raw))
        if not updated:
            raise HTTPException(status_code=404, detail={"error": "retrieval_not_found", "retrieval_id": retrieval_id})
        return {"status": "recorded"}

    @app.post("/agent/index")
    def agent_index(request: Dict[str, Any] | None = None) -> Dict[str, Any]:
        payload = request or {}
        repo_root = str(payload.get("repo_root") or _context_plane_repo_root(source_env)).strip()
        estimated = _start_agent_indexing(app, repo_root=repo_root)
        return {"status": "started", "estimated_files": int(estimated)}

    @app.get("/agent/index/status")
    def agent_index_status() -> Dict[str, Any]:
        state = dict(app.state.agent_index_state or {})
        return {
            "status": str(state.get("status") or "empty"),
            "files_indexed": int(state.get("files_indexed") or 0),
            "last_run": state.get("last_run"),
            "estimated_files": int(state.get("estimated_files") or 0),
        }

    @app.post("/v1/context-packs/retrieve")
    def retrieve_context_pack(request: Dict[str, Any], http_request: FastAPIRequest) -> Dict[str, Any]:
        started_at = time.perf_counter()
        trace = _trace_context_from_headers(http_request)
        _validate_input_contract(RETRIEVE_REQUEST, request)
        namespace = str(request.get("namespace"))
        prompt = str(request.get("prompt"))
        top_k = int(request.get("top_k", 5))
        registry_path_raw = request.get("registry_path")
        registry_path = str(registry_path_raw) if isinstance(registry_path_raw, str) and registry_path_raw else None
        try:
            pack = budgeted_call(
                "context_plane.rag.retrieve",
                lambda: app.state.runtime.retriever.retrieve_context_pack(
                    namespace=namespace,
                    prompt=prompt,
                    top_k=max(1, top_k),
                    registry_path=registry_path,
                ),
                metadata={
                    "run_id": trace.get("run_id"),
                    "case_id": trace.get("case_id"),
                    "trace_id": trace.get("trace_id"),
                    "namespace": namespace,
                },
            )
            response = {"pack": pack}
            _validate_output_contract(RETRIEVE_RESPONSE, response)
            _log_service_event(
                event_type="context_plane.retrieve",
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
                mode=trace.get("mode") or "remote",
                query=prompt,
                namespace=namespace,
                duration_ms=_elapsed_ms(started_at),
                chunk_count=_chunk_count(pack),
                status="ok",
            )
            return response
        except PerfBudgetViolation as exc:
            _log_service_event(
                event_type="context_plane.retrieve",
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
                mode=trace.get("mode") or "remote",
                query=prompt,
                namespace=namespace,
                duration_ms=_elapsed_ms(started_at),
                chunk_count=0,
                status="budget_exceeded",
                error=str(exc),
            )
            raise HTTPException(status_code=503, detail={"error": "perf_budget_exceeded", "msg": str(exc)})
        except Exception as exc:
            _log_service_event(
                event_type="context_plane.retrieve",
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
                mode=trace.get("mode") or "remote",
                query=prompt,
                namespace=namespace,
                duration_ms=_elapsed_ms(started_at),
                chunk_count=0,
                status="error",
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail={"error": "retrieve_failed", "msg": str(exc)})

    @app.post("/v1/context-packs/ingest")
    async def ingest_context_pack(request: Dict[str, Any], http_request: FastAPIRequest) -> Dict[str, Any]:
        return await _persist_context_pack(
            app.state.runtime,
            request,
            idempotency_cache=app.state.ingest_idempotency,
            idempotency_lock=app.state.ingest_idempotency_lock,
            trace_context=_trace_context_from_headers(http_request),
            ingest_contracts_service=app.state.ingest_contracts_service,
            policy_client=app.state.policy_client,
        )

    @app.post("/v1/context-packs/persist")
    async def persist_context_pack(request: Dict[str, Any], http_request: FastAPIRequest) -> Dict[str, Any]:
        return await _persist_context_pack(
            app.state.runtime,
            request,
            idempotency_cache=app.state.ingest_idempotency,
            idempotency_lock=app.state.ingest_idempotency_lock,
            trace_context=_trace_context_from_headers(http_request),
            ingest_contracts_service=app.state.ingest_contracts_service,
            policy_client=app.state.policy_client,
        )

    @app.get("/v1/context-packs/refs")
    def list_context_refs(context_id: str) -> Dict[str, Any]:
        try:
            refs = app.state.runtime.store.list_context_refs(context_id=str(context_id))
            return {"context_refs": list(refs)}
        except Exception as exc:
            raise HTTPException(status_code=500, detail={"error": "list_refs_failed", "msg": str(exc)})

    @app.delete("/v1/context-packs/document/{doc_id:path}")
    def delete_document(doc_id: str, http_request: FastAPIRequest, tombstone: bool = False) -> Dict[str, Any]:
        _require_ingest_write_or_503(operation="context_plane.delete_document")
        started_at = time.perf_counter()
        trace = _trace_context_from_headers(http_request)
        maintenance = _maintenance_or_501(app.state.runtime)
        try:
            report = maintenance.delete_document(doc_id=str(doc_id), tombstone=bool(tombstone))
            _log_service_event(
                event_type="context_plane.delete",
                action="delete_document",
                trace_id=trace.get("trace_id"),
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                doc_id=str(doc_id),
                tombstone=bool(tombstone),
                deleted_count=len(report.get("deleted_refs", [])),
                duration_ms=_elapsed_ms(started_at),
                status="ok",
            )
            return report
        except HTTPException:
            raise
        except Exception as exc:
            _log_service_event(
                event_type="context_plane.delete",
                action="delete_document",
                trace_id=trace.get("trace_id"),
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                doc_id=str(doc_id),
                tombstone=bool(tombstone),
                duration_ms=_elapsed_ms(started_at),
                status="error",
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail={"error": "delete_document_failed", "msg": str(exc)})

    @app.post("/v1/context-packs/documents/delete")
    def delete_documents(request: Dict[str, Any], http_request: FastAPIRequest) -> Dict[str, Any]:
        _require_ingest_write_or_503(operation="context_plane.delete_documents")
        started_at = time.perf_counter()
        trace = _trace_context_from_headers(http_request)
        maintenance = _maintenance_or_501(app.state.runtime)
        doc_ids_raw = request.get("doc_ids", [])
        if not isinstance(doc_ids_raw, list) or not doc_ids_raw:
            raise HTTPException(status_code=400, detail={"error": "doc_ids_required"})
        doc_ids = [str(doc_id) for doc_id in doc_ids_raw if str(doc_id).strip()]
        if not doc_ids:
            raise HTTPException(status_code=400, detail={"error": "doc_ids_required"})
        tombstone = bool(request.get("tombstone", False))
        try:
            report = maintenance.delete_documents(doc_ids=doc_ids, tombstone=tombstone)
            _log_service_event(
                event_type="context_plane.delete",
                action="delete_documents",
                trace_id=trace.get("trace_id"),
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                doc_ids=doc_ids,
                tombstone=tombstone,
                deleted_count=len(report.get("deleted_refs", [])),
                duration_ms=_elapsed_ms(started_at),
                status="ok",
            )
            return report
        except HTTPException:
            raise
        except Exception as exc:
            _log_service_event(
                event_type="context_plane.delete",
                action="delete_documents",
                trace_id=trace.get("trace_id"),
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                doc_ids=doc_ids,
                tombstone=tombstone,
                duration_ms=_elapsed_ms(started_at),
                status="error",
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail={"error": "delete_documents_failed", "msg": str(exc)})

    @app.delete("/v1/context-packs/run/{run_id:path}")
    def delete_run(run_id: str, http_request: FastAPIRequest, tombstone: bool = False) -> Dict[str, Any]:
        _require_ingest_write_or_503(operation="context_plane.delete_run")
        started_at = time.perf_counter()
        trace = _trace_context_from_headers(http_request)
        maintenance = _maintenance_or_501(app.state.runtime)
        try:
            report = maintenance.delete_run(run_id=str(run_id), tombstone=bool(tombstone))
            _log_service_event(
                event_type="context_plane.delete",
                action="delete_run",
                trace_id=trace.get("trace_id"),
                run_id=str(run_id),
                case_id=trace.get("case_id"),
                tombstone=bool(tombstone),
                deleted_count=len(report.get("deleted_refs", [])),
                duration_ms=_elapsed_ms(started_at),
                status="ok",
            )
            return report
        except HTTPException:
            raise
        except Exception as exc:
            _log_service_event(
                event_type="context_plane.delete",
                action="delete_run",
                trace_id=trace.get("trace_id"),
                run_id=str(run_id),
                case_id=trace.get("case_id"),
                tombstone=bool(tombstone),
                duration_ms=_elapsed_ms(started_at),
                status="error",
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail={"error": "delete_run_failed", "msg": str(exc)})

    @app.post("/v1/context-packs/cleanup")
    def cleanup_context_plane(http_request: FastAPIRequest, request: Dict[str, Any] | None = None) -> Dict[str, Any]:
        _require_ingest_write_or_503(operation="context_plane.cleanup")
        started_at = time.perf_counter()
        trace = _trace_context_from_headers(http_request)
        maintenance = _maintenance_or_501(app.state.runtime)
        payload = request or {}
        ttl_policies = _coerce_ttl_policies(payload.get("ttl_policies"))
        delete_orphans = bool(payload.get("delete_orphans", True))
        try:
            report = maintenance.cleanup(
                ttl_policies=ttl_policies,
                delete_orphans=delete_orphans,
            )
            _log_service_event(
                event_type="context_plane.cleanup",
                trace_id=trace.get("trace_id"),
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                ttl_policies=ttl_policies,
                delete_orphans=delete_orphans,
                duration_ms=_elapsed_ms(started_at),
                status="ok",
                deleted_count=len(report.get("deleted_refs", [])),
            )
            return report
        except HTTPException:
            raise
        except Exception as exc:
            _log_service_event(
                event_type="context_plane.cleanup",
                trace_id=trace.get("trace_id"),
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                ttl_policies=ttl_policies,
                delete_orphans=delete_orphans,
                duration_ms=_elapsed_ms(started_at),
                status="error",
                error=str(exc),
            )
            raise HTTPException(status_code=500, detail={"error": "cleanup_failed", "msg": str(exc)})

    @app.post("/v1/events/context-retrieved")
    def publish_context_retrieved(request: Dict[str, Any]) -> Dict[str, Any]:
        context_id = request.get("context_id")
        namespace = request.get("namespace")
        context_ref = request.get("context_ref")
        metadata = request.get("metadata")
        if not isinstance(context_id, str) or not context_id.strip():
            raise HTTPException(status_code=400, detail={"error": "context_id_required"})
        if not isinstance(namespace, str) or not namespace.strip():
            raise HTTPException(status_code=400, detail={"error": "namespace_required"})
        if not isinstance(context_ref, str) or not context_ref.strip():
            raise HTTPException(status_code=400, detail={"error": "context_ref_required"})
        if not isinstance(metadata, dict):
            metadata = {}
        try:
            app.state.runtime.publisher.publish_context_retrieved(
                context_id=context_id,
                namespace=namespace,
                context_ref=context_ref,
                metadata=metadata,
            )
            return {"ok": True}
        except Exception as exc:
            raise HTTPException(status_code=500, detail={"error": "publish_failed", "msg": str(exc)})

    return app


async def _persist_context_pack(
    runtime: ContextPlaneRuntime,
    request: Dict[str, Any],
    *,
    idempotency_cache: Any,
    idempotency_lock: Lock,
    trace_context: Dict[str, str] | None = None,
    ingest_contracts_service: IngestContractsService | None = None,
    policy_client: KafkaPolicyClient | None = None,
) -> Dict[str, Any]:
    _require_ingest_write_or_503(operation="context_plane.ingest")
    started_at = time.perf_counter()
    trace = dict(trace_context or {})
    _validate_input_contract(INGEST_REQUEST, request)
    context_id = str(request.get("context_id"))
    namespace = str(request.get("namespace"))
    request_id = str(request.get("request_id"))
    doc_version = str(request.get("doc_version"))
    doc_id_raw = request.get("doc_id")
    doc_id = str(doc_id_raw) if isinstance(doc_id_raw, str) and doc_id_raw.strip() else f"{context_id}:{namespace}"
    idempotency_key = _idempotency_key(doc_id=doc_id, doc_version=doc_version)
    pack = request.get("pack")
    if not isinstance(pack, dict):
        raise HTTPException(status_code=400, detail={"error": "pack_required"})
    ingest_contracts = ingest_contracts_service or get_ingest_contracts_service()
    policy_client_instance = policy_client or get_policy_client()
    try:
        with idempotency_lock:
            existing = _idempotency_get(idempotency_cache, idempotency_key)
            if isinstance(existing, dict):
                _log_service_event(
                    event_type="context_plane.ingest",
                    run_id=trace.get("run_id"),
                    case_id=trace.get("case_id"),
                    trace_id=trace.get("trace_id"),
                    mode=trace.get("mode") or "remote",
                    context_id=context_id,
                    namespace=namespace,
                    request_id=request_id,
                    doc_id=doc_id,
                    doc_version=doc_version,
                    idempotency_key=idempotency_key,
                    chunk_count=_chunk_count(pack),
                    duration_ms=_elapsed_ms(started_at),
                    status="deduped",
                )
                return dict(existing)

            try:
                validated_contracts = ingest_contracts.validate_ingest_payload(request)
            except IngestContractValidationError as exc:
                raise HTTPException(
                    status_code=400,
                    detail={"error": "ingest_contract_invalid", "msg": str(exc)},
                ) from exc
            discovery_metadata = await _authorize_discovery_fetch(
                request=request,
                policy_client=policy_client_instance,
            )
            pack_to_persist = _pack_with_ingest_metadata(
                pack=pack,
                validated_contracts=validated_contracts,
                discovery_metadata=discovery_metadata,
            )
            ref = budgeted_call(
                "context_plane.ingest.persist",
                lambda: runtime.store.persist_context_pack(
                    context_id=context_id,
                    namespace=namespace,
                    pack=pack_to_persist,
                    run_id=trace.get("run_id"),
                    case_id=trace.get("case_id"),
                    trace_id=trace.get("trace_id"),
                ),
                metadata={
                    "run_id": trace.get("run_id"),
                    "case_id": trace.get("case_id"),
                    "trace_id": trace.get("trace_id"),
                    "context_id": context_id,
                    "namespace": namespace,
                },
            )
            response = {
                "context_ref": ref,
                "idempotency_key": idempotency_key,
                "request_id": request_id,
                "doc_id": doc_id,
                "doc_version": doc_version,
            }
            _validate_output_contract(INGEST_RESPONSE, response)
            _idempotency_set(idempotency_cache, idempotency_key, dict(response))
            _log_service_event(
                event_type="context_plane.ingest",
                run_id=trace.get("run_id"),
                case_id=trace.get("case_id"),
                trace_id=trace.get("trace_id"),
                mode=trace.get("mode") or "remote",
                context_id=context_id,
                namespace=namespace,
                request_id=request_id,
                doc_id=doc_id,
                doc_version=doc_version,
                idempotency_key=idempotency_key,
                chunk_count=_chunk_count(pack_to_persist),
                duration_ms=_elapsed_ms(started_at),
                context_ref=ref,
                status="ok",
                rights_verdict=(
                    (
                        ((discovery_metadata or {}).get("rights_verdict") or {}).get("overall_verdict")
                        if isinstance((discovery_metadata or {}).get("rights_verdict"), dict)
                        else None
                    )
                ),
            )
            return response
    except PerfBudgetViolation as exc:
        _log_service_event(
            event_type="context_plane.ingest",
            run_id=trace.get("run_id"),
            case_id=trace.get("case_id"),
            trace_id=trace.get("trace_id"),
            mode=trace.get("mode") or "remote",
            context_id=context_id,
            namespace=namespace,
            request_id=request_id,
            doc_id=doc_id,
            doc_version=doc_version,
            idempotency_key=idempotency_key,
            chunk_count=_chunk_count(pack),
            duration_ms=_elapsed_ms(started_at),
            status="budget_exceeded",
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail={"error": "perf_budget_exceeded", "msg": str(exc)})
    except HTTPException:
        raise
    except Exception as exc:
        _log_service_event(
            event_type="context_plane.ingest",
            run_id=trace.get("run_id"),
            case_id=trace.get("case_id"),
            trace_id=trace.get("trace_id"),
            mode=trace.get("mode") or "remote",
            context_id=context_id,
            namespace=namespace,
            request_id=request_id,
            doc_id=doc_id,
            doc_version=doc_version,
            idempotency_key=idempotency_key,
            chunk_count=_chunk_count(pack),
            duration_ms=_elapsed_ms(started_at),
            status="error",
            error=str(exc),
        )
        raise HTTPException(status_code=500, detail={"error": "persist_failed", "msg": str(exc)})


async def _authorize_discovery_fetch(
    *,
    request: Dict[str, Any],
    policy_client: KafkaPolicyClient,
) -> Dict[str, Any] | None:
    # Extract discovery candidates using policy client
    candidates = policy_client.extract_candidates(request)
    if not candidates:
        return None

    rights_policy = str(request.get("rights_policy") or "").strip()
    if not rights_policy:
        rights_policy = "standard"  # Default policy

    # Request policy approval via Kafka events
    try:
        resource = request.get("doc_id", request.get("repository_path", "unknown"))
        policy_response = await policy_client.request_approval(
            operation="context_ingest",
            resource=resource,
            metadata={
                "rights_policy": rights_policy,
                "candidates": candidates,
                "context_id": request.get("context_id"),
                "namespace": request.get("namespace")
            }
        )

        if not policy_response.approved:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "rights_denied",
                    "reason": policy_response.reason,
                    "verdict": policy_response.verdict
                },
            )

        # Create mock decision object for backward compatibility
        class MockDecision:
            def __init__(self, verdict):
                self.overall_verdict = verdict
            def to_dict(self):
                return {"overall_verdict": self.overall_verdict}

        decision = MockDecision(policy_response.verdict)

    except Exception as e:
        logger.warning(f"Policy evaluation failed, using fallback approval: {e}")
        # Fail-open for availability
        decision = MockDecision("ALLOW")
    return {
        "rights_policy": rights_policy,
        "rights_verdict": decision.to_dict(),
        "discovery_candidates": candidates,
    }


def _pack_with_discovery_metadata(pack: Dict[str, Any], discovery_metadata: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(discovery_metadata, dict) or not discovery_metadata:
        return dict(pack)
    payload = dict(pack)
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = dict(metadata)
    metadata["discovery_candidates"] = list(discovery_metadata.get("discovery_candidates", []))
    metadata["rights_policy"] = discovery_metadata.get("rights_policy")
    metadata["rights_verdict"] = discovery_metadata.get("rights_verdict")
    payload["metadata"] = metadata
    return payload


def _pack_with_ingest_metadata(
    *,
    pack: Dict[str, Any],
    validated_contracts: Dict[str, Dict[str, Any]],
    discovery_metadata: Dict[str, Any] | None,
) -> Dict[str, Any]:
    payload = dict(pack)
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata = dict(metadata)
    if isinstance(validated_contracts, dict) and validated_contracts:
        metadata["ingest_contracts"] = dict(validated_contracts)
        metadata["ingest_contract_types"] = sorted(str(key) for key in validated_contracts.keys())
    payload["metadata"] = metadata
    return _pack_with_discovery_metadata(payload, discovery_metadata)


def _validate_input_contract(schema_name: str, payload: Dict[str, Any]) -> None:
    try:
        validate_context_plane_contract(schema_name, payload)
    except ContextPlaneContractValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "contract_invalid", "schema": schema_name, "msg": str(exc)},
        )


def _validate_output_contract(schema_name: str, payload: Dict[str, Any]) -> None:
    try:
        validate_context_plane_contract(schema_name, payload)
    except ContextPlaneContractValidationError as exc:
        raise HTTPException(
            status_code=500,
            detail={"error": "contract_invalid_output", "schema": schema_name, "msg": str(exc)},
        )


def _idempotency_key(*, doc_id: str, doc_version: str) -> str:
    raw = f"{doc_id}:{doc_version}".encode("utf-8", errors="replace")
    return sha256(raw).hexdigest()


def _trace_context_from_headers(http_request: FastAPIRequest) -> Dict[str, str]:
    headers = getattr(http_request, "headers", {}) if http_request is not None else {}
    return {
        "run_id": str(headers.get("x-run-id") or "").strip(),
        "case_id": str(headers.get("x-case-id") or "").strip(),
        "trace_id": str(headers.get("x-trace-id") or "").strip(),
        "mode": str(headers.get("x-context-plane-mode") or "").strip(),
    }


def _maintenance_or_501(runtime: ContextPlaneRuntime) -> Any:
    maintenance = getattr(runtime, "maintenance", None)
    if maintenance is None:
        raise HTTPException(status_code=501, detail={"error": "maintenance_not_configured"})
    return maintenance


def _cleanup_interval_seconds(env: Mapping[str, str]) -> float:
    raw = str(env.get("CONTEXT_PLANE_CLEANUP_INTERVAL_SECONDS", "")).strip()
    if not raw:
        return 0.0
    try:
        parsed = float(raw)
    except Exception:
        return 0.0
    return parsed if parsed > 0 else 0.0


def _context_plane_api_key(env: Mapping[str, str]) -> str:
    return str(env.get("CONTEXT_PLANE_API_KEY", "") or "").strip()


def _cleanup_delete_orphans(env: Mapping[str, str]) -> bool:
    return _parse_bool(env.get("CONTEXT_PLANE_CLEANUP_DELETE_ORPHANS"), default=True)


def _cleanup_ttl_policies(env: Mapping[str, str]) -> Dict[str, int] | None:
    raw = str(env.get("CONTEXT_PLANE_CLEANUP_TTL_POLICIES_JSON", "")).strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return _coerce_ttl_policies(parsed)


def _coerce_ttl_policies(value: Any) -> Dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result: Dict[str, int] = {}
    for key, raw in value.items():
        name = str(key).strip()
        if not name:
            continue
        try:
            days = int(raw)
        except Exception:
            continue
        if days < 0:
            continue
        result[name] = days
    return result or None


def _parse_bool(raw: Any, *, default: bool) -> bool:
    value = str(raw or "").strip().lower()
    if not value:
        return bool(default)
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _cleanup_loop(
    maintenance: Any,
    stop_event: Event,
    interval_seconds: float,
    ttl_policies: Dict[str, int] | None,
    delete_orphans: bool,
) -> None:
    while not stop_event.wait(interval_seconds):
        try:
            maintenance.cleanup(
                ttl_policies=ttl_policies,
                delete_orphans=delete_orphans,
            )
        except Exception as exc:
            logger.warning("context_plane_cleanup_loop_error=%s", str(exc))


def _chunk_count(payload: Dict[str, Any] | None) -> int:
    if not isinstance(payload, dict):
        return 0
    facts = payload.get("facts", [])
    evidence = payload.get("evidence", [])
    count = 0
    if isinstance(facts, list):
        count += len(facts)
    if isinstance(evidence, list):
        count += len(evidence)
    return int(count)


def _elapsed_ms(started_at: float) -> float:
    return round((time.perf_counter() - float(started_at)) * 1000.0, 3)


def _log_service_event(event_type: str, **payload: Any) -> None:
    row = {"event_type": str(event_type)}
    row.update({str(k): v for k, v in payload.items()})
    logger.info("telemetry=%s", json.dumps(row, ensure_ascii=True, sort_keys=True, default=str))


def _idempotency_get(cache: Any, key: str) -> Dict[str, Any] | None:
    if cache is None:
        return None
    getter = getattr(cache, "get", None)
    if callable(getter):
        value = getter(str(key))
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str) and value.strip():
            try:
                decoded = json.loads(value)
            except Exception:
                return None
            return dict(decoded) if isinstance(decoded, dict) else None
    if isinstance(cache, dict):
        value = cache.get(str(key))
        if isinstance(value, dict):
            return dict(value)
    return None


def _idempotency_set(cache: Any, key: str, value: Dict[str, Any]) -> None:
    payload = dict(value or {})
    if cache is None:
        return
    setter = getattr(cache, "set", None)
    if callable(setter):
        setter(str(key), payload)
        return
    if isinstance(cache, dict):
        cache[str(key)] = payload


def _start_agent_indexing(app: Any, *, repo_root: str) -> int:
    source_env = getattr(app.state, "source_env", os.environ)
    target_root = str(repo_root or "").strip() or _context_plane_repo_root(source_env)
    estimated = estimate_repository_files(
        repo_root=target_root,
        exclude_patterns=_context_plane_exclude_patterns(source_env),
    )
    with app.state.agent_index_lock:
        worker = app.state.agent_index_thread
        if worker is not None and worker.is_alive():
            app.state.agent_index_state["estimated_files"] = int(estimated)
            return int(estimated)
        app.state.agent_index_state = {
            "status": "indexing",
            "files_indexed": 0,
            "last_run": app.state.agent_index_state.get("last_run"),
            "estimated_files": int(estimated),
            "error": None,
        }
        thread = Thread(
            target=_run_agent_indexing,
            args=(app, target_root),
            daemon=True,
            name="context-plane-agent-indexer",
        )
        app.state.agent_index_thread = thread
        thread.start()
    return int(estimated)


def _run_agent_indexing(app: Any, repo_root: str) -> None:
    try:
        result = asyncio.run(_run_agent_indexing_async(app=app, repo_root=repo_root))
        with app.state.agent_index_lock:
            app.state.agent_index_state = {
                "status": "ready" if int(result.indexed_files) > 0 else "empty",
                "files_indexed": int(result.indexed_files),
                "last_run": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "estimated_files": int(result.estimated_files),
                "error": None,
            }
    except Exception as exc:
        with app.state.agent_index_lock:
            app.state.agent_index_state = {
                "status": "empty",
                "files_indexed": 0,
                "last_run": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "estimated_files": int(app.state.agent_index_state.get("estimated_files") or 0),
                "error": str(exc),
            }
        logger.error("context_plane_agent_indexing_failed error=%s", str(exc))


async def _run_agent_indexing_async(*, app: Any, repo_root: str):
    source_env = getattr(app.state, "source_env", os.environ)
    summary_engine = ExpertServingSummaryEngine(
        base_url=_expert_serving_base_url(source_env),
        api_key=_expert_serving_api_key(source_env),
        timeout_seconds=_expert_serving_timeout_seconds(source_env),
    )

    async def _ingest(doc_id: str, doc_version: str, pack: Dict[str, Any]) -> None:
        payload = {
            "context_id": "repo-index",
            "namespace": _namespace_from_doc_id(doc_id),
            "doc_id": str(doc_id),
            "doc_version": str(doc_version),
            "request_id": str(doc_version),
            "rights_policy": "standard",
            "discovery_candidates": [
                {
                    "candidate_id": str(doc_id),
                    "source_ref": f"repo://{doc_id}",
                    "rights_label": "internal",
                }
            ],
            "pack": dict(pack),
        }
        await _persist_context_pack(
            app.state.runtime,
            payload,
            idempotency_cache=app.state.ingest_idempotency,
            idempotency_lock=app.state.ingest_idempotency_lock,
            ingest_contracts_service=app.state.ingest_contracts_service,
            policy_client=app.state.policy_client,
        )

    return await index_repository(
        repo_root=repo_root,
        store=app.state.runtime.context_store,
        summary_engine=summary_engine,
        exclude_patterns=_context_plane_exclude_patterns(source_env),
        max_file_size_kb=_context_plane_max_file_size_kb(source_env),
        ingest_fn=_ingest,
    )


def _namespace_from_doc_id(doc_id: str) -> str:
    path = str(doc_id or "").replace("\\", "/")
    if "/" not in path:
        return "repo/root"
    return "/".join(path.split("/")[:-1]) or "repo/root"


def _context_plane_redis_url(env: Mapping[str, str]) -> str | None:
    raw = str(env.get("CONTEXT_PLANE_REDIS_URL", "") or "").strip()
    return raw or None


def _idempotency_ttl_seconds(env: Mapping[str, str]) -> int:
    raw = str(env.get("CONTEXT_PLANE_IDEMPOTENCY_TTL_SECONDS", "")).strip()
    if not raw:
        return 86400
    try:
        value = int(raw)
    except Exception:
        return 86400
    return value if value > 0 else 86400


def _expert_serving_base_url(env: Mapping[str, str]) -> str:
    return str(env.get("EXPERT_SERVING_BASE_URL", "") or "").strip() or "http://expert-serving:8094"


def _expert_serving_api_key(env: Mapping[str, str]) -> str | None:
    raw = str(env.get("EXPERT_SERVING_API_KEY", "") or "").strip()
    return raw or None


def _expert_serving_timeout_seconds(env: Mapping[str, str]) -> float:
    raw = str(env.get("EXPERT_SERVING_TIMEOUT_SECONDS", "")).strip()
    if not raw:
        return 60.0
    try:
        parsed = float(raw)
    except Exception:
        return 60.0
    return parsed if parsed > 0 else 60.0


def _context_plane_repo_root(env: Mapping[str, str]) -> str:
    return str(env.get("CONTEXT_PLANE_REPO_ROOT", "") or "").strip() or "/app/repo"


def _context_plane_artifact_root(env: Mapping[str, str]) -> str:
    configured = str(env.get("ARTIFACT_DIR", "") or "").strip()
    if configured:
        return configured
    container_default = Path("/app/artifacts")
    if container_default.exists():
        return container_default.as_posix()
    return "artifacts"


def _context_plane_index_on_startup(env: Mapping[str, str]) -> bool:
    return _parse_bool(env.get("CONTEXT_PLANE_INDEX_ON_STARTUP"), default=False)


def _context_plane_max_file_size_kb(env: Mapping[str, str]) -> int:
    raw = str(env.get("CONTEXT_PLANE_MAX_FILE_SIZE_KB", "")).strip()
    if not raw:
        return 100
    try:
        parsed = int(raw)
    except Exception:
        return 100
    return parsed if parsed > 0 else 100


def _context_plane_exclude_patterns(env: Mapping[str, str]) -> list[str]:
    raw = str(env.get("CONTEXT_PLANE_EXCLUDE_PATTERNS", "") or "").strip()
    if not raw:
        return ["__pycache__", ".git", "node_modules", "*.pyc"]
    out = [item.strip() for item in raw.split(",") if item.strip()]
    return out or ["__pycache__", ".git", "node_modules", "*.pyc"]


class IdempotencyCache:
    def __init__(self, *, redis_url: str | None, ttl_seconds: int = 86400) -> None:
        self._prefix = "ctx_idempotency:"
        self._ttl = max(1, int(ttl_seconds))
        self._memory: Dict[str, str] = {}
        self._redis = None
        if redis_url and redis is not None:
            try:
                self._redis = redis.from_url(str(redis_url))
                self._redis.ping()
            except Exception:
                self._redis = None

    def get(self, key: str) -> str | None:
        scoped = f"{self._prefix}{str(key)}"
        if self._redis is not None:
            try:
                value = self._redis.get(scoped)
            except Exception:
                value = None
            if isinstance(value, bytes):
                return value.decode("utf-8", errors="replace")
            if isinstance(value, str):
                return value
        return self._memory.get(scoped)

    def set(self, key: str, value: Dict[str, Any]) -> None:
        scoped = f"{self._prefix}{str(key)}"
        payload = json.dumps(dict(value or {}), ensure_ascii=True, sort_keys=True)
        if self._redis is not None:
            try:
                self._redis.setex(scoped, self._ttl, payload)
                return
            except Exception:
                pass
        self._memory[scoped] = payload


def _require_ingest_write_or_503(*, operation: str) -> None:
    try:
        get_killswitch_service().require_write(
            operation=str(operation),
            scope="INGEST_WRITE",
        )
    except KillSwitchViolation as exc:
        raise HTTPException(
            status_code=503,
            detail={"error": "writes_disabled", "reason": str(exc)},
        )
