"""FastAPI routes for indexing and querying RepoGraph."""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Annotated

from fastapi import APIRouter, FastAPI, HTTPException, Header
from pydantic import BaseModel

from repograph import __version__
from repograph.graph import get_graph_store
from repograph.graph.factory import GraphStore
from repograph.indexer import parse_file, walk
from repograph.indexer.schema import AT_LINE, DEFINES, IN_FILE
from repograph.connectors.obsidian.service import ObsidianService

DEFAULT_DB_BACKEND = os.getenv("REPOGRAPH_DB_BACKEND", "cog")
DEFAULT_DB_PATH = os.getenv("REPOGRAPH_DB_PATH", ".repograph")

router = APIRouter()
obsidian_service = ObsidianService()


class IndexRequest(BaseModel):
    repo_path: str
    force: bool = False


def create_app() -> FastAPI:
    app = FastAPI(title="RepoGraph", version=__version__)
    app.include_router(router)
    return app


@router.post("/index")
def index_repo(request: IndexRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    start = time.perf_counter()
    repo_path = Path(request.repo_path).expanduser().resolve()
    store = _get_store(x_tenant_id)
    metadata = _load_metadata(store)

    if metadata["repo_path"] and metadata["repo_path"] != str(repo_path) and not request.force:
        raise HTTPException(
            status_code=409,
            detail="Graph already contains a different repo. Re-run with force=true to replace it.",
        )

    try:
        if request.force:
            store.clear()

        files_indexed = 0
        triples_added = 0
        for filepath, language in walk(str(repo_path)):
            triples = parse_file(filepath, language, repo_path=repo_path)
            if triples:
                store.put_triples_batch(triples)
                triples_added += len(triples)
            files_indexed += 1
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ModuleNotFoundError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Indexing failed: {exc}") from exc

    last_indexed = _utc_now_iso()
    _save_metadata(
        store,
        {
            "repo_path": str(repo_path),
            "last_indexed": last_indexed,
        },
    )

    duration_ms = int((time.perf_counter() - start) * 1000)
    return {
        "status": "ok",
        "files_indexed": files_indexed,
        "triples_added": triples_added,
        "duration_ms": duration_ms,
    }


@router.get("/status")
def status(x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    store = _get_store(x_tenant_id)
    metadata = _load_metadata(store)
    stats = store.stats()
    return {
        "indexed": metadata["last_indexed"] is not None,
        "repo_path": metadata["repo_path"],
        "node_count": stats["node_count"],
        "last_indexed": metadata["last_indexed"],
    }


@router.get("/symbols")
def symbols(q: str, limit: int = 20, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, list[str]]:
    store = _get_store(x_tenant_id)
    return {"symbols": store.search(q, limit=limit)}


@router.get("/symbol/{symbol_path}")
def symbol_detail(symbol_path: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    store = _get_store(x_tenant_id)
    symbol_exists = store.has_symbol(symbol_path)
    in_file = store.first_outgoing(symbol_path, IN_FILE)
    at_line = store.first_outgoing(symbol_path, AT_LINE)
    calls = store.callees_of(symbol_path)
    called_by = store.callers_of(symbol_path)
    defines = store.outgoing(symbol_path, DEFINES)
    defined_by = _first_or_none(store.incoming(symbol_path, DEFINES))

    if not symbol_exists and not any([in_file, at_line, calls, called_by, defines, defined_by]):
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol_path}")

    return {
        "symbol": symbol_path,
        "in_file": in_file,
        "at_line": at_line,
        "calls": calls,
        "called_by": called_by,
        "defines": defines,
        "defined_by": defined_by,
    }


@router.get("/blast-radius/{symbol_path}")
def blast_radius(symbol_path: str, depth: int = 3, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    store = _get_store(x_tenant_id)
    return {
        "symbol": symbol_path,
        "depth": depth,
        "affected": store.blast_radius(symbol_path, depth=depth),
    }


@router.get("/blast-radius-with-context/{symbol_path}")
def blast_radius_with_context(symbol_path: str, depth: int = 3, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    base = blast_radius(symbol_path, depth=depth, x_tenant_id=x_tenant_id)
    result = obsidian_service.search_notes_by_symbol(symbol_path)
    base["notes_context"] = {
        "status": result.status,
        "notes": [n.model_dump() for n in result.notes]
    }
    return base


@router.get("/notes/search")
def search_notes(q: str) -> dict[str, Any]:
    result = obsidian_service.search_notes_by_query(q)
    return {"notes": [n.model_dump() for n in result.notes], "status": result.status}


@router.get("/notes/for-symbol/{symbol_path}")
def notes_for_symbol(symbol_path: str) -> dict[str, Any]:
    result = obsidian_service.search_notes_by_symbol(symbol_path)
    return {"notes": [n.model_dump() for n in result.notes], "status": result.status}


@router.get("/file/{filepath:path}")
def file_detail(filepath: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    store = _get_store(x_tenant_id)
    return {
        "filepath": filepath,
        "symbols": store.file_symbols(filepath),
    }


_USAGE_TENANT = "llm-usage"
_PRED_ROUTED_TO = "routed_to"
_PRED_CAPABILITY = "capability"
_PRED_LATENCY = "latency_s"
_PRED_TOKENS_IN = "tokens_in"
_PRED_TOKENS_OUT = "tokens_out"
_PRED_ROUTED_AT = "routed_at"


class UsageLogRequest(BaseModel):
    model_id: str
    capability: str
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


@router.post("/usage/log")
def log_usage(req: UsageLogRequest) -> dict[str, str]:
    store = _get_store(_USAGE_TENANT)
    rid = f"request:{uuid.uuid4()}"
    now = _utc_now_iso()
    store.put_triples_batch([
        (rid, _PRED_ROUTED_TO, f"model:{req.model_id}"),
        (rid, _PRED_CAPABILITY, req.capability),
        (rid, _PRED_LATENCY, str(round(req.latency_s, 3))),
        (rid, _PRED_TOKENS_IN, str(req.input_tokens)),
        (rid, _PRED_TOKENS_OUT, str(req.output_tokens)),
        (rid, _PRED_ROUTED_AT, now),
    ])
    return {"id": rid, "routed_at": now}


@router.get("/usage/stats")
def usage_stats() -> dict[str, Any]:
    store = _get_store(_USAGE_TENANT)
    raw = store.g.v().out(_PRED_ROUTED_TO).all().get("result", [])
    model_counts: dict[str, int] = {}
    for entry in raw:
        mid = entry.get("id", "")
        if mid.startswith("model:"):
            model_counts[mid[6:]] = model_counts.get(mid[6:], 0) + 1

    total = sum(model_counts.values())
    stats = [
        {"model_id": m, "requests": c, "share_pct": round(c / total * 100, 1) if total else 0}
        for m, c in sorted(model_counts.items(), key=lambda x: -x[1])
    ]
    return {"total_requests": total, "models": stats}


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


# ---------------------------------------------------------------------------
# Fase 9 — 10 new REST endpoints
# ---------------------------------------------------------------------------

# 1. POST /task/classify  (INTEGRATION_POINTS contract)
class ClassifyRequest(BaseModel):
    query: str
    hint: str | None = None

@router.post("/task/classify")
def task_classify(req: ClassifyRequest) -> dict[str, Any]:
    from repograph.task_families import get_or_default
    from repograph.retrieval.task_planner import classify
    family = classify(req.query, hint=req.hint)
    meta = get_or_default(family)
    return {
        "task_family": family,
        "description": meta.description,
        "defaults": {"token_budget": meta.token_budget, "coarse_limit": meta.coarse_limit, "expand_limit": meta.expand_limit},
    }


# 2. POST /retrieve/coarse  (INTEGRATION_POINTS contract)
class CoarseRequest(BaseModel):
    query: str
    task_family: str | None = None
    limit: int = 40

@router.post("/retrieve/coarse")
def retrieve_coarse(req: CoarseRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.retrieval.task_planner import classify
    from repograph.retrieval.coarse_retriever import coarse_retrieve
    store = _get_store(x_tenant_id)
    family = req.task_family or classify(req.query)
    symbols = coarse_retrieve(req.query, family, store, limit=req.limit)
    return {"task_family": family, "symbols": symbols, "count": len(symbols)}


# 3. POST /retrieve/structural  (INTEGRATION_POINTS contract)
class StructuralRequest(BaseModel):
    symbols: list[str]
    task_family: str = "symbol_lookup"
    max_symbols: int = 80

@router.post("/retrieve/structural")
def retrieve_structural(req: StructuralRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.retrieval.structural_expander import expand
    store = _get_store(x_tenant_id)
    expanded = expand(req.symbols, req.task_family, store, max_symbols=req.max_symbols)
    return {"task_family": req.task_family, "symbols": expanded, "count": len(expanded), "added": len(expanded) - len(req.symbols)}


# 4. GET /metrics/dashboard — success metrics per PROGRAM_REPOGRAPH section 8
@router.get("/metrics/dashboard")
def metrics_dashboard(x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    store = _get_store(x_tenant_id)
    return _build_metrics(store)


# 5. GET /metrics/retrieval — retrieval trace stats
@router.get("/metrics/retrieval")
def metrics_retrieval(limit: int = 100, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    store = _get_store(x_tenant_id)
    traces = store.search("retrieval:", limit=limit)
    family_counts: dict[str, int] = {}
    token_totals: list[int] = []
    for trace_id in traces:
        family = store.first_outgoing(trace_id, "retrieval_task_family")
        if family:
            family_counts[family] = family_counts.get(family, 0) + 1
        tok = store.first_outgoing(trace_id, "retrieval_token_estimate")
        if tok and tok.isdigit():
            token_totals.append(int(tok))
    avg_tokens = int(sum(token_totals) / len(token_totals)) if token_totals else 0
    return {
        "total_retrievals": len(traces),
        "by_family": family_counts,
        "avg_token_estimate": avg_tokens,
    }


# 6. GET /graph/stats — extended graph statistics
@router.get("/graph/stats")
def graph_stats(x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.indexer.schema import BELONGS_TO_SERVICE, IS_TEST, RISK_LEVEL
    store = _get_store(x_tenant_id)
    stats = store.stats()
    high_risk = len(store.incoming("high", RISK_LEVEL))
    test_symbols = len(store.incoming("true", IS_TEST))
    metadata = _load_metadata(store)
    return {
        "node_count": stats["node_count"],
        "high_risk_symbols": high_risk,
        "test_symbols": test_symbols,
        "repo_path": metadata.get("repo_path"),
        "last_indexed": metadata.get("last_indexed"),
    }


# 7. GET /knowledge/adr — list ADR nodes
@router.get("/knowledge/adr")
def list_adrs(x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.indexer.schema import DOC_TITLE, DOC_TYPE
    store = _get_store(x_tenant_id)
    adr_nodes = store.incoming("adr", DOC_TYPE)
    adrs = [{"node": n, "title": store.first_outgoing(n, DOC_TITLE)} for n in adr_nodes]
    return {"adrs": adrs, "count": len(adrs)}


# 8. GET /knowledge/ci — list CI job nodes
@router.get("/knowledge/ci")
def list_ci_jobs(x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.indexer.schema import CI_JOB_NAME, DOC_TYPE
    store = _get_store(x_tenant_id)
    ci_nodes = store.incoming("ci_workflow", DOC_TYPE)
    jobs = [{"node": n, "job_name": store.first_outgoing(n, CI_JOB_NAME)} for n in ci_nodes]
    return {"ci_jobs": jobs, "count": len(jobs)}


# 9. POST /memory/task/{id}/complete — convenience complete endpoint
@router.post("/memory/task/{task_id}/complete")
def complete_task(task_id: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.memory import store
    s = _get_store(x_tenant_id)
    record = store.set_status(s, task_id, "completed")
    if not record:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return {"task_id": task_id, "status": "completed"}


# 10. GET /integration/discovery — INTEGRATION_POINTS discovery response
@router.get("/integration/discovery")
def integration_discovery() -> dict[str, Any]:
    store_ok = True
    try:
        from repograph.graph import get_graph_store
        get_graph_store(backend="cog", db_path=DEFAULT_DB_PATH)
    except Exception:
        store_ok = False
    return {
        "program": "repograph",
        "contract_version_supported": "v1.0",
        "contract_version_min_required": "v1.0",
        "available": {
            "repograph_mcp_server_running": True,
            "repograph_working_set_builder_available": store_ok,
            "repograph_summaries_available": store_ok,
            "repograph_verifier_available": True,
            "repograph_task_memory_available": store_ok,
            "repograph_knowledge_graph_available": store_ok,
        },
        "phases_complete": [1, 2, 3, 4, 5, 6, 7, 8, 9],
    }


def _build_metrics(store) -> dict[str, Any]:
    from repograph.indexer.schema import RISK_LEVEL
    stats = store.stats()
    high = len(store.incoming("high", RISK_LEVEL))
    med = len(store.incoming("medium", RISK_LEVEL))
    low = len(store.incoming("low", RISK_LEVEL))
    traces = store.search("retrieval:", limit=500)
    token_totals = []
    for t in traces:
        tok = store.first_outgoing(t, "retrieval_token_estimate")
        if tok and tok.isdigit():
            token_totals.append(int(tok))
    avg_tokens = int(sum(token_totals) / len(token_totals)) if token_totals else 0
    tasks = store.search("task:", limit=200)
    completed = sum(1 for t in tasks if store.first_outgoing(t, "memory_status") == "completed")
    return {
        "graph": {"node_count": stats["node_count"], "risk_high": high, "risk_medium": med, "risk_low": low},
        "retrieval": {"total_traces": len(traces), "avg_token_estimate": avg_tokens},
        "tasks": {"total": len(tasks), "completed": completed},
        "targets": {
            "token_reduction_target": ">50% vs flat retrieval",
            "retrieval_precision_target": ">80%",
            "irrelevant_files_target": "<10%",
        },
    }


# ---------------------------------------------------------------------------
# Fase 8: Knowledge graph
# ---------------------------------------------------------------------------

class KnowledgeIndexRequest(BaseModel):
    repo_path: str
    include: list[str] | None = None  # docs | ownership | config | ci


@router.post("/knowledge/index")
def knowledge_index(req: KnowledgeIndexRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Index knowledge graph (docs, ownership, config, CI) for a repository."""
    from pathlib import Path as _Path
    from repograph.knowledge import index_knowledge
    if not _Path(req.repo_path).is_dir():
        raise HTTPException(status_code=400, detail=f"repo_path is not a directory: {req.repo_path}")
    store = _get_store(x_tenant_id)
    result = index_knowledge(
        repo_path=req.repo_path,
        store=store,
        include=set(req.include) if req.include else None,
    )
    return {
        "status": "ok",
        "total_triples": result.total,
        "docs_triples": result.docs_triples,
        "ownership_triples": result.ownership_triples,
        "config_triples": result.config_triples,
        "ci_triples": result.ci_triples,
        "duration_ms": result.duration_ms,
    }


@router.get("/knowledge/docs/{filepath:path}")
def docs_for_file(filepath: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Return doc nodes that mention symbols in a file."""
    from repograph.indexer.schema import DOCUMENTED_BY, MENTIONED_IN_DOC
    store = _get_store(x_tenant_id)
    symbols = store.file_symbols(filepath)
    doc_nodes: set[str] = set()
    for sym in symbols:
        doc_nodes.update(store.outgoing(sym, MENTIONED_IN_DOC))
        doc_nodes.update(store.outgoing(sym, DOCUMENTED_BY))
    return {"filepath": filepath, "doc_nodes": list(doc_nodes)}


@router.get("/knowledge/owners/{filepath:path}")
def owners_for_file(filepath: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Return CODEOWNERS entries for a file."""
    from repograph.indexer.schema import OWNED_BY
    store = _get_store(x_tenant_id)
    owners = store.outgoing(filepath, OWNED_BY)
    return {"filepath": filepath, "owners": owners}


# ---------------------------------------------------------------------------
# Fase 7: Verifier — POST /verify/patch-plan (INTEGRATION_POINTS contract)
# ---------------------------------------------------------------------------

class VerifyRequest(BaseModel):
    repo_path: str
    files: list[str]
    symbols: list[str] = []
    task_id: str | None = None
    steps: list[str] | None = None   # subset: dependency|lint|type_check|test|static_analysis|smoke


@router.post("/verify/patch-plan")
def verify_patch_plan(req: VerifyRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Run verification toolchain on changed files. Feeds back to TaskMemory if task_id given."""
    from pathlib import Path as _Path
    from repograph.verifier import verify

    # Safety: only allow paths that exist as directories
    if not _Path(req.repo_path).is_dir():
        raise HTTPException(status_code=400, detail=f"repo_path is not a directory: {req.repo_path}")

    store = _get_store(x_tenant_id) if req.task_id else None
    result = verify(
        repo_path=req.repo_path,
        files=req.files,
        symbols=req.symbols,
        store=store,
        task_id=req.task_id,
        steps=req.steps,
    )
    return result.model_dump()


# ---------------------------------------------------------------------------
# Fase 6: TaskMemory — POST /memory/task/update (INTEGRATION_POINTS contract)
# ---------------------------------------------------------------------------

class TaskMemoryCreateRequest(BaseModel):
    query: str
    task_family: str
    working_set_id: str = ""
    retrieval_id: str = ""


class TaskMemoryUpdateRequest(BaseModel):
    task_id: str
    consumer_accepted: bool | None = None
    patch_applied: bool | None = None
    verification_passed: bool | None = None
    status: str | None = None
    notes: str | None = None


class PatchRecordRequest(BaseModel):
    diff_summary: str
    symbols_touched: list[str] = []
    verification_result: str | None = None
    failure_reason: str | None = None


class TestFailureRequest(BaseModel):
    test_symbol: str
    failure_message: str


@router.post("/memory/task")
def create_task_memory(req: TaskMemoryCreateRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.memory import store
    tenant = x_tenant_id or "default"
    s = _get_store(x_tenant_id)
    record = store.create(s, req.query, req.task_family, req.working_set_id, req.retrieval_id)
    # dual-write to Postgres
    _pg_task_memory().create(
        req.query, req.task_family, req.working_set_id, req.retrieval_id, tenant_id=tenant
    )
    return record.model_dump()


@router.post("/memory/task/update")
def update_task_memory(req: TaskMemoryUpdateRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Update task memory with precision signals — matches INTEGRATION_POINTS /memory/task/update contract."""
    from repograph.memory import store
    from repograph.memory.models import PrecisionSignals
    s = _get_store(x_tenant_id)
    pg = _pg_task_memory()
    if req.consumer_accepted is not None or req.patch_applied is not None or req.verification_passed is not None:
        signals = PrecisionSignals(
            consumer_accepted=req.consumer_accepted,
            patch_applied=req.patch_applied,
            verification_passed=req.verification_passed,
        )
        record = store.update_signals(s, req.task_id, signals)
        pg.update_signals(req.task_id, signals)
    else:
        record = store.get(s, req.task_id)
    if record and req.status:
        record = store.set_status(s, req.task_id, req.status)
        pg.set_status(req.task_id, req.status)
    if not record:
        raise HTTPException(status_code=404, detail=f"Task not found: {req.task_id}")
    return record.model_dump()


@router.get("/memory/task")
def list_task_memory(limit: int = 20, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.memory import store
    tenant = x_tenant_id or "default"
    pg = _pg_task_memory()
    pg_records = pg.list_recent(tenant_id=tenant, limit=limit)
    if pg_records:
        return {"tasks": [r.model_dump() for r in pg_records], "count": len(pg_records), "source": "postgres"}
    s = _get_store(x_tenant_id)
    records = store.list_recent(s, limit=limit)
    return {"tasks": [r.model_dump() for r in records], "count": len(records), "source": "graph"}


@router.get("/memory/task/{task_id}")
def get_task_memory(task_id: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.memory import store
    pg = _pg_task_memory()
    record = pg.get(task_id)
    if record:
        return {**record.model_dump(), "source": "postgres"}
    s = _get_store(x_tenant_id)
    record = store.get(s, task_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return {**record.model_dump(), "source": "graph"}


@router.post("/memory/task/{task_id}/patch")
def record_patch(task_id: str, req: PatchRecordRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.memory import store
    from repograph.memory.models import PatchRecord
    import uuid
    from datetime import datetime, timezone
    tenant = x_tenant_id or "default"
    s = _get_store(x_tenant_id)
    patch = PatchRecord(
        patch_id=f"patch:{uuid.uuid4()}",
        attempted_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        diff_summary=req.diff_summary,
        symbols_touched=req.symbols_touched,
        verification_result=req.verification_result,
        failure_reason=req.failure_reason,
    )
    record = store.add_patch(s, task_id, patch)
    _pg_task_memory().add_patch(task_id, patch, tenant_id=tenant)
    if not record:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return {"task_id": task_id, "patch_id": patch.patch_id, "patches_total": record.patches_attempted}


@router.post("/memory/task/{task_id}/test-failure")
def record_test_failure(task_id: str, req: TestFailureRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.memory import store
    from repograph.memory.models import TestFailureRecord
    from datetime import datetime, timezone
    tenant = x_tenant_id or "default"
    s = _get_store(x_tenant_id)
    failure = TestFailureRecord(
        test_symbol=req.test_symbol,
        failure_message=req.failure_message,
        recorded_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    record = store.add_test_failure(s, task_id, failure)
    _pg_task_memory().add_test_failure(task_id, failure, tenant_id=tenant)
    if not record:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return {"task_id": task_id, "test_failures_total": len(record.test_failures)}


@router.get("/memory/task/{task_id}/patch-prompt")
def get_patch_prompt(task_id: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Return a minimal-patch specialist prompt for this task, including retry context if applicable."""
    from repograph.memory import store, get_preamble
    s = _get_store(x_tenant_id)
    record = store.get(s, task_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    is_retry = record.patches_attempted > 0
    preamble = get_preamble(record.task_family, is_retry=is_retry)
    last_failure = None
    last_diff = None
    if is_retry and record.patches:
        last = record.patches[-1]
        last_failure = last.failure_reason
        last_diff = last.diff_summary
    return {
        "task_id": task_id,
        "is_retry": is_retry,
        "patches_attempted": record.patches_attempted,
        "preamble": preamble,
        "last_failure": last_failure,
        "last_diff_summary": last_diff,
    }


# ---------------------------------------------------------------------------
# Fase 5: Task-family registry endpoint
# ---------------------------------------------------------------------------

@router.get("/task-families")
def list_task_families() -> dict[str, Any]:
    from repograph.task_families import list_all
    return {
        "families": [
            {
                "name": f.name,
                "description": f.description,
                "token_budget": f.token_budget,
                "coarse_limit": f.coarse_limit,
                "expand_limit": f.expand_limit,
            }
            for f in list_all()
        ]
    }


@router.get("/task-families/{name}")
def get_task_family(name: str) -> dict[str, Any]:
    from repograph.task_families import get
    family = get(name)
    if not family:
        raise HTTPException(status_code=404, detail=f"Unknown task family: {name}")
    return {
        "name": family.name,
        "description": family.description,
        "token_budget": family.token_budget,
        "coarse_limit": family.coarse_limit,
        "expand_limit": family.expand_limit,
        "priority_edges": list(family.priority_edges),
        "prompt_preamble": family.prompt_preamble,
    }


# ---------------------------------------------------------------------------
# Fase 4: WorkingSet
# ---------------------------------------------------------------------------

class WorkingSetRequest(BaseModel):
    query: str
    task_hint: str | None = None
    token_budget: int = 4096
    coarse_limit: int = 40
    expand_limit: int = 80
    format: str = "full"   # full | compact | prompt


@router.post("/working-set")
def build_working_set(req: WorkingSetRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Build a token-budget-aware WorkingSet from a query."""
    from repograph.working_set import build, to_compact, to_prompt_context
    store = _get_store(x_tenant_id)
    ws = build(
        query=req.query,
        store=store,
        task_hint=req.task_hint,
        token_budget=req.token_budget,
        coarse_limit=req.coarse_limit,
        expand_limit=req.expand_limit,
    )
    if req.format == "compact":
        return to_compact(ws)
    if req.format == "prompt":
        return {"prompt_context": to_prompt_context(ws), "token_estimate": ws.token_estimate}
    return ws.model_dump()


@router.get("/symbol/{symbol_path}/enrichment")
def symbol_enrichment(symbol_path: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Return Fase 1 enrichment fields for a symbol."""
    from repograph.indexer.schema import BELONGS_TO_SERVICE, IS_ENTRYPOINT, IS_TEST, RISK_LEVEL, SERVICE_NAME, SIGNATURE
    store = _get_store(x_tenant_id)
    return {
        "symbol": symbol_path,
        "service_name": store.first_outgoing(symbol_path, SERVICE_NAME),
        "belongs_to_service": store.first_outgoing(symbol_path, BELONGS_TO_SERVICE),
        "risk_level": store.first_outgoing(symbol_path, RISK_LEVEL),
        "is_test": store.first_outgoing(symbol_path, IS_TEST) == "true",
        "is_entrypoint": store.first_outgoing(symbol_path, IS_ENTRYPOINT) == "true",
        "signature": store.first_outgoing(symbol_path, SIGNATURE),
    }


@router.get("/service/{service_name}/symbols")
def service_symbols(service_name: str, limit: int = 50, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Return all symbols belonging to a service."""
    from repograph.indexer.schema import BELONGS_TO_SERVICE
    store = _get_store(x_tenant_id)
    symbols = store.incoming(service_name, BELONGS_TO_SERVICE)
    return {"service": service_name, "symbols": symbols[:limit], "total": len(symbols)}


@router.get("/risk/{level}")
def symbols_by_risk(level: str, limit: int = 50, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Return symbols at a given risk level (low | medium | high)."""
    from repograph.indexer.schema import RISK_LEVEL
    store = _get_store(x_tenant_id)
    symbols = store.incoming(level, RISK_LEVEL)
    return {"risk_level": level, "symbols": symbols[:limit], "total": len(symbols)}


# ---------------------------------------------------------------------------
# Fase 2: Summary store + retrieve + summary-input
# ---------------------------------------------------------------------------

class SummaryWriteRequest(BaseModel):
    text: str


@router.put("/summary/symbol/{symbol_path}")
def write_symbol_summary(symbol_path: str, req: SummaryWriteRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, str]:
    from repograph.indexer.schema import SHORT_SUMMARY
    from repograph.cache import redis as rcache, keys as rkeys
    tenant = x_tenant_id or "default"
    store = _get_store(x_tenant_id)
    store.put_triple(symbol_path, SHORT_SUMMARY, req.text)
    rcache.delete(rkeys.summary_symbol(tenant, _repo_path(store), symbol_path))
    return {"symbol": symbol_path, "status": "ok"}


@router.put("/summary/file/{filepath:path}")
def write_file_summary(filepath: str, req: SummaryWriteRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, str]:
    from repograph.indexer.schema import FILE_SUMMARY
    from repograph.cache import redis as rcache, keys as rkeys
    tenant = x_tenant_id or "default"
    store = _get_store(x_tenant_id)
    store.put_triple(f"file:{filepath}", FILE_SUMMARY, req.text)
    rcache.delete(rkeys.summary_file(tenant, _repo_path(store), filepath))
    return {"filepath": filepath, "status": "ok"}


@router.put("/summary/service/{service_name}")
def write_service_summary(service_name: str, req: SummaryWriteRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, str]:
    from repograph.indexer.schema import SERVICE_SUMMARY
    from repograph.cache import redis as rcache, keys as rkeys
    tenant = x_tenant_id or "default"
    store = _get_store(x_tenant_id)
    store.put_triple(f"service:{service_name}", SERVICE_SUMMARY, req.text)
    rcache.delete(rkeys.summary_service(tenant, _repo_path(store), service_name))
    return {"service": service_name, "status": "ok"}


@router.put("/summary/repo")
def write_repo_summary(req: SummaryWriteRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, str]:
    from repograph.indexer.schema import REPO_NODE, REPO_SUMMARY
    from repograph.cache import redis as rcache, keys as rkeys
    tenant = x_tenant_id or "default"
    store = _get_store(x_tenant_id)
    store.put_triple(REPO_NODE, REPO_SUMMARY, req.text)
    rcache.delete(rkeys.summary_l0(tenant, _repo_path(store)))
    return {"status": "ok"}


@router.get("/summary/symbol/{symbol_path}")
def read_symbol_summary(symbol_path: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.indexer.schema import SHORT_SUMMARY
    from repograph.cache import redis as rcache, keys as rkeys
    tenant = x_tenant_id or "default"
    store = _get_store(x_tenant_id)
    key = rkeys.summary_symbol(tenant, _repo_path(store), symbol_path)
    cached = rcache.get(key)
    if cached is not None:
        return {"symbol": symbol_path, "summary": cached, "cache": "hit"}
    summary = store.first_outgoing(symbol_path, SHORT_SUMMARY)
    if summary:
        rcache.set(key, summary)
    return {"symbol": symbol_path, "summary": summary, "cache": "miss"}


@router.get("/summary/file/{filepath:path}")
def read_file_summary(filepath: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.indexer.schema import FILE_SUMMARY
    from repograph.cache import redis as rcache, keys as rkeys
    tenant = x_tenant_id or "default"
    store = _get_store(x_tenant_id)
    key = rkeys.summary_file(tenant, _repo_path(store), filepath)
    cached = rcache.get(key)
    if cached is not None:
        return {"filepath": filepath, "summary": cached, "cache": "hit"}
    summary = store.first_outgoing(f"file:{filepath}", FILE_SUMMARY)
    if summary:
        rcache.set(key, summary)
    return {"filepath": filepath, "summary": summary, "cache": "miss"}


@router.get("/summary/service/{service_name}")
def read_service_summary(service_name: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.indexer.schema import SERVICE_SUMMARY
    from repograph.cache import redis as rcache, keys as rkeys
    tenant = x_tenant_id or "default"
    store = _get_store(x_tenant_id)
    key = rkeys.summary_service(tenant, _repo_path(store), service_name)
    cached = rcache.get(key)
    if cached is not None:
        return {"service": service_name, "summary": cached, "cache": "hit"}
    summary = store.first_outgoing(f"service:{service_name}", SERVICE_SUMMARY)
    if summary:
        rcache.set(key, summary)
    return {"service": service_name, "summary": summary, "cache": "miss"}


@router.get("/summary/repo")
def read_repo_summary(x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.indexer.schema import REPO_NODE, REPO_SUMMARY
    from repograph.cache import redis as rcache, keys as rkeys
    tenant = x_tenant_id or "default"
    store = _get_store(x_tenant_id)
    key = rkeys.summary_l0(tenant, _repo_path(store))
    cached = rcache.get(key)
    if cached is not None:
        return {"summary": cached, "cache": "hit"}
    summary = store.first_outgoing(REPO_NODE, REPO_SUMMARY)
    if summary:
        rcache.set(key, summary)
    return {"summary": summary, "cache": "miss"}


@router.get("/summary-input/file/{filepath:path}")
def summary_input_file(filepath: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Return structured raw data for a file so a consumer can generate a FILE_SUMMARY."""
    from repograph.indexer.schema import AT_LINE, BELONGS_TO_SERVICE, RISK_LEVEL, SIGNATURE, SHORT_SUMMARY
    store = _get_store(x_tenant_id)
    symbols = store.file_symbols(filepath)
    symbol_data = []
    for sym in symbols:
        symbol_data.append({
            "symbol": sym,
            "at_line": store.first_outgoing(sym, AT_LINE),
            "signature": store.first_outgoing(sym, SIGNATURE),
            "risk_level": store.first_outgoing(sym, RISK_LEVEL),
            "calls": store.callees_of(sym),
            "called_by": store.callers_of(sym),
            "existing_summary": store.first_outgoing(sym, SHORT_SUMMARY),
        })
    return {
        "filepath": filepath,
        "service": store.first_outgoing(f"file:{filepath}", BELONGS_TO_SERVICE),
        "symbols": symbol_data,
        "symbol_count": len(symbols),
    }


@router.get("/summary-input/service/{service_name}")
def summary_input_service(service_name: str, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Return structured data for a service so a consumer can generate a SERVICE_SUMMARY."""
    from repograph.indexer.schema import BELONGS_TO_SERVICE, FILE_SUMMARY
    store = _get_store(x_tenant_id)
    symbols = store.incoming(service_name, BELONGS_TO_SERVICE)
    files: dict[str, Any] = {}
    for sym in symbols:
        fp = store.first_outgoing(sym, "in_file") or store.first_outgoing(sym, "IN_FILE")
        if fp and fp not in files:
            files[fp] = store.first_outgoing(f"file:{fp}", FILE_SUMMARY)
    return {
        "service": service_name,
        "files": [{"filepath": fp, "existing_summary": s} for fp, s in files.items()],
        "file_count": len(files),
        "symbol_count": len(symbols),
    }


# ---------------------------------------------------------------------------
# Fase 3: Multi-stage retrieval
# ---------------------------------------------------------------------------

class RetrieveRequest(BaseModel):
    query: str
    task_hint: str | None = None
    token_budget: int = 4096
    coarse_limit: int = 40
    expand_limit: int = 80
    persist_trace: bool = True


@router.post("/retrieve")
def multi_stage_retrieve(req: RetrieveRequest, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Multi-stage retrieval: classify → coarse → structural → fine selection."""
    from repograph.retrieval import retrieve
    store = _get_store(x_tenant_id)
    result = retrieve(
        query=req.query,
        store=store,
        task_hint=req.task_hint,
        token_budget=req.token_budget,
        coarse_limit=req.coarse_limit,
        expand_limit=req.expand_limit,
        persist_trace=req.persist_trace,
    )
    return {
        "retrieval_id": result.retrieval_id,
        "task_family": result.task_family,
        "stages": result.stages,
        "working_set": result.working_set,
        "token_estimate": result.token_estimate,
        "files": result.files,
        "duration_ms": result.duration_ms,
    }


@router.get("/summary-input/repo")
def summary_input_repo(x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """Return structured data for the whole repo so a consumer can generate a REPO_SUMMARY."""
    from repograph.indexer.schema import BELONGS_TO_SERVICE, SERVICE_SUMMARY
    store = _get_store(x_tenant_id)
    metadata = _load_metadata(store)
    stats = store.stats()
    all_symbols = store.outgoing("__repo__", BELONGS_TO_SERVICE) or []
    services: dict[str, Any] = {}
    for sym in store.search("", limit=5000):
        svc = store.first_outgoing(sym, BELONGS_TO_SERVICE)
        if svc and svc not in services:
            services[svc] = store.first_outgoing(f"service:{svc}", SERVICE_SUMMARY)
    return {
        "repo_path": metadata.get("repo_path"),
        "last_indexed": metadata.get("last_indexed"),
        "node_count": stats["node_count"],
        "services": [{"service": svc, "existing_summary": s} for svc, s in services.items()],
        "service_count": len(services),
    }


@router.post("/shared-retrieval/prepare")
def shared_retrieval_prepare(
    body: dict,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    from repograph.shared_retrieval import SharedRetrievalRequest, prepare_task_context
    from repograph.shared_retrieval.adapters import format_for_consumer
    if x_tenant_id:
        body.setdefault("tenant_id", x_tenant_id)
    req = SharedRetrievalRequest(**body)
    store = _get_store(req.tenant_id if req.tenant_id != "default" else x_tenant_id)
    response = prepare_task_context(req, store)
    consumer = req.consumer
    return format_for_consumer(response, consumer)


@router.post("/shared-retrieval/working-set")
def shared_retrieval_working_set(
    body: dict,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    from repograph.shared_retrieval import SharedRetrievalRequest, prepare_task_context
    if x_tenant_id:
        body.setdefault("tenant_id", x_tenant_id)
    req = SharedRetrievalRequest(**body)
    store = _get_store(req.tenant_id if req.tenant_id != "default" else x_tenant_id)
    response = prepare_task_context(req, store)
    return response.working_set


@router.post("/shared-retrieval/prompt-pack")
def shared_retrieval_prompt_pack(
    body: dict,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    from repograph.shared_retrieval import SharedRetrievalRequest, prepare_task_context
    if x_tenant_id:
        body.setdefault("tenant_id", x_tenant_id)
    req = SharedRetrievalRequest(**body)
    store = _get_store(req.tenant_id if req.tenant_id != "default" else x_tenant_id)
    response = prepare_task_context(req, store)
    return response.prompt_pack.model_dump()


@router.post("/shared-retrieval/retry-pack")
def shared_retrieval_retry_pack(
    body: dict,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    from repograph.shared_retrieval import SharedRetrievalRequest, prepare_task_context
    from repograph.shared_retrieval.profiles import get_profile
    from repograph.shared_retrieval.prompt_packer import pack
    if x_tenant_id:
        body.setdefault("tenant_id", x_tenant_id)
    failure_reason = body.pop("failure_reason", "")
    previous_diff = body.pop("previous_diff", None)
    req = SharedRetrievalRequest(**body)
    store = _get_store(req.tenant_id if req.tenant_id != "default" else x_tenant_id)
    response = prepare_task_context(req, store)
    profile = get_profile(req.output_profile)
    from repograph.working_set.builder import build as build_ws
    ws = build_ws(query=req.query, store=store, task_hint=req.task_hint, token_budget=req.target_context)
    retry_pack = pack(ws, profile, failure_reason=failure_reason, previous_diff=previous_diff)
    return retry_pack.model_dump()


@router.post("/cache/invalidate")
def cache_invalidate(body: dict, x_tenant_id: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    from repograph.cache import redis as redis_layer
    from repograph.cache import keys as cache_keys
    tenant = x_tenant_id or body.get("tenant_id", "default")
    repo_path = body.get("repo_path", "")
    if not repo_path:
        raise HTTPException(status_code=400, detail="repo_path required")
    prefix = cache_keys._repo_prefix(tenant, repo_path)
    deleted = redis_layer.delete_pattern(f"{prefix}:*")
    return {"deleted_keys": deleted, "prefix": prefix}


@router.get("/shared-retrieval/status")
def shared_retrieval_status() -> dict[str, Any]:
    from repograph.cache import redis as redis_layer
    from repograph.postgres import tracer as pg_tracer
    return {
        "cache": redis_layer.status(),
        "postgres": pg_tracer.status(),
        "profiles": ["tiny", "small", "medium", "patch", "review"],
        "strategies": ["summary_first", "symbol_first", "patch_first", "test_first", "retry"],
        "compressor_passes": ["none", "drop_calls", "drop_low_summaries", "drop_low_risk"],
    }


@router.get("/postgres/status")
def postgres_status() -> dict[str, Any]:
    from repograph.postgres import tracer as pg_tracer
    return pg_tracer.status()


@router.post("/postgres/migrate")
def postgres_migrate() -> dict[str, Any]:
    from repograph.postgres.migrate import run as run_migrations
    import io, logging
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    logging.getLogger("repograph.postgres.migrate").addHandler(handler)
    try:
        run_migrations()
        return {"ok": True, "log": buf.getvalue()}
    except SystemExit as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        logging.getLogger("repograph.postgres.migrate").removeHandler(handler)


def _repo_path(store: GraphStore) -> str:
    meta = store.load_metadata()
    return meta.get("repo_path") or ""


def _pg_task_memory():
    from repograph.postgres.repositories.task_memory import TaskMemoryRepository
    return TaskMemoryRepository()


def _get_store(tenant_id: str | None = None) -> GraphStore:
    db_path = DEFAULT_DB_PATH
    if tenant_id and DEFAULT_DB_BACKEND == "cog":
        db_path = f"{DEFAULT_DB_PATH}_{tenant_id}"

    return get_graph_store(
        backend=DEFAULT_DB_BACKEND,
        db_path=db_path,
    )


def _load_metadata(store: GraphStore) -> dict[str, str | None]:
    return store.load_metadata()


def _save_metadata(store: GraphStore, metadata: dict[str, str]) -> None:
    store.save_metadata(metadata)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _first_or_none(values: list[str]) -> str | None:
    return values[0] if values else None


app = create_app()
