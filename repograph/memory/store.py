"""TaskMemory store — persists and recalls task memory records in the graph."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from repograph.graph.factory import GraphStore

from .models import (
    PatchRecord,
    PrecisionSignals,
    TaskMemoryRecord,
    TestFailureRecord,
)

_PRED_TYPE = "memory_type"
_PRED_QUERY = "memory_query"
_PRED_FAMILY = "memory_task_family"
_PRED_WS_ID = "memory_working_set_id"
_PRED_RET_ID = "memory_retrieval_id"
_PRED_CREATED = "memory_created_at"
_PRED_UPDATED = "memory_updated_at"
_PRED_STATUS = "memory_status"
_PRED_SIGNALS = "memory_signals_json"
_PRED_PATCHES = "memory_patches_json"
_PRED_FAILURES = "memory_failures_json"
_PRED_NOTES = "memory_notes"

_MEMORY_NODE_TYPE = "task_memory"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def create(
    store: GraphStore,
    query: str,
    task_family: str,
    working_set_id: str = "",
    retrieval_id: str = "",
) -> TaskMemoryRecord:
    task_id = f"task:{uuid.uuid4()}"
    now = _now()
    record = TaskMemoryRecord(
        task_id=task_id,
        query=query,
        task_family=task_family,
        working_set_id=working_set_id,
        retrieval_id=retrieval_id,
        created_at=now,
        updated_at=now,
    )
    _write(store, record)
    return record


def get(store: GraphStore, task_id: str) -> TaskMemoryRecord | None:
    raw = store.first_outgoing(task_id, _PRED_QUERY)
    if not raw:
        return None
    return _read(store, task_id)


def update_signals(store: GraphStore, task_id: str, signals: PrecisionSignals) -> TaskMemoryRecord | None:
    record = get(store, task_id)
    if not record:
        return None
    record.signals = signals
    record.updated_at = _now()
    if signals.verification_passed is True and signals.consumer_accepted is True:
        record.status = "completed"
    _write(store, record)
    return record


def add_patch(store: GraphStore, task_id: str, patch: PatchRecord) -> TaskMemoryRecord | None:
    record = get(store, task_id)
    if not record:
        return None
    record.patches.append(patch)
    record.updated_at = _now()
    _write(store, record)
    return record


def add_test_failure(store: GraphStore, task_id: str, failure: TestFailureRecord) -> TaskMemoryRecord | None:
    record = get(store, task_id)
    if not record:
        return None
    record.test_failures.append(failure)
    record.updated_at = _now()
    _write(store, record)
    return record


def set_status(store: GraphStore, task_id: str, status: str) -> TaskMemoryRecord | None:
    record = get(store, task_id)
    if not record:
        return None
    record.status = status
    record.updated_at = _now()
    _write(store, record)
    return record


def list_recent(store: GraphStore, limit: int = 20) -> list[TaskMemoryRecord]:
    nodes = store.incoming(_MEMORY_NODE_TYPE, _PRED_TYPE)
    records = []
    for node_id in nodes[:limit]:
        r = get(store, node_id)
        if r:
            records.append(r)
    records.sort(key=lambda r: r.updated_at, reverse=True)
    return records


def _write(store: GraphStore, record: TaskMemoryRecord) -> None:
    triples = [
        (record.task_id, _PRED_TYPE, _MEMORY_NODE_TYPE),
        (record.task_id, _PRED_QUERY, record.query),
        (record.task_id, _PRED_FAMILY, record.task_family),
        (record.task_id, _PRED_WS_ID, record.working_set_id),
        (record.task_id, _PRED_RET_ID, record.retrieval_id),
        (record.task_id, _PRED_CREATED, record.created_at),
        (record.task_id, _PRED_UPDATED, record.updated_at),
        (record.task_id, _PRED_STATUS, record.status),
        (record.task_id, _PRED_SIGNALS, record.signals.model_dump_json()),
        (record.task_id, _PRED_PATCHES, json.dumps([p.model_dump() for p in record.patches])),
        (record.task_id, _PRED_FAILURES, json.dumps([f.model_dump() for f in record.test_failures])),
        (record.task_id, _PRED_NOTES, record.notes),
    ]
    store.put_triples_batch(triples)


def _read(store: GraphStore, task_id: str) -> TaskMemoryRecord:
    def _get(pred: str) -> str:
        return store.first_outgoing(task_id, pred) or ""

    signals_raw = _get(_PRED_SIGNALS)
    patches_raw = _get(_PRED_PATCHES)
    failures_raw = _get(_PRED_FAILURES)

    return TaskMemoryRecord(
        task_id=task_id,
        query=_get(_PRED_QUERY),
        task_family=_get(_PRED_FAMILY),
        working_set_id=_get(_PRED_WS_ID),
        retrieval_id=_get(_PRED_RET_ID),
        created_at=_get(_PRED_CREATED),
        updated_at=_get(_PRED_UPDATED),
        status=_get(_PRED_STATUS) or "active",
        signals=PrecisionSignals.model_validate_json(signals_raw) if signals_raw else PrecisionSignals(),
        patches=[PatchRecord(**p) for p in json.loads(patches_raw)] if patches_raw else [],
        test_failures=[TestFailureRecord(**f) for f in json.loads(failures_raw)] if failures_raw else [],
        notes=_get(_PRED_NOTES),
    )
