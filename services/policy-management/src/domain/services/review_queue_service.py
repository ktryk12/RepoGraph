from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping
import json
import os
import time

import yaml

from babyai_shared.fingerprint import canonical_json, sha256_json
from policy.constitution_service import get_constitution_service
from babyai_shared.storage.safe_paths import safe_segment


DEFAULT_REVIEW_QUEUE_PATH = Path(__file__).with_name("review_queue.yaml")
DEFAULT_REVIEW_QUEUE_ROOT = Path("artifacts") / "review_queue"
_KNOWN_STATUSES = ("pending", "reviewed", "expired")


class ReviewQueueNotFoundError(KeyError):
    pass


@dataclass(frozen=True)
class ReviewQueueState:
    path: Path
    schema_version: int
    version: str
    sla_days: int
    expiry_days: int


@dataclass(frozen=True)
class ReviewQueueRequest:
    subject_id: str
    reason: str
    payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    source: str | None = None
    review_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_id", _required_text(self.subject_id, name="subject_id"))
        object.__setattr__(self, "reason", _required_text(self.reason, name="reason"))
        object.__setattr__(self, "payload", _as_dict(self.payload))
        object.__setattr__(self, "metadata", _as_dict(self.metadata))
        object.__setattr__(self, "source", _optional_text(self.source))
        object.__setattr__(self, "review_id", _optional_text(self.review_id))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "reason": self.reason,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
            "source": self.source,
            "review_id": self.review_id,
        }


@dataclass(frozen=True)
class ReviewQueueEntry:
    review_id: str
    status: str
    subject_id: str
    reason: str
    payload: Dict[str, Any]
    metadata: Dict[str, Any]
    source: str | None
    created_at: str
    sla_due_at: str
    expires_at: str
    reviewed_at: str | None = None
    reviewer: str | None = None
    resolution: str | None = None
    expired_at: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "review_id": self.review_id,
            "status": self.status,
            "subject_id": self.subject_id,
            "reason": self.reason,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
            "source": self.source,
            "created_at": self.created_at,
            "sla_due_at": self.sla_due_at,
            "expires_at": self.expires_at,
            "reviewed_at": self.reviewed_at,
            "reviewer": self.reviewer,
            "resolution": self.resolution,
            "expired_at": self.expired_at,
        }

    @staticmethod
    def from_dict(payload: Mapping[str, Any]) -> "ReviewQueueEntry":
        return ReviewQueueEntry(
            review_id=_required_text(payload.get("review_id"), name="review_id"),
            status=_required_text(payload.get("status"), name="status").lower(),
            subject_id=_required_text(payload.get("subject_id"), name="subject_id"),
            reason=_required_text(payload.get("reason"), name="reason"),
            payload=_as_dict(payload.get("payload")),
            metadata=_as_dict(payload.get("metadata")),
            source=_optional_text(payload.get("source")),
            created_at=_required_text(payload.get("created_at"), name="created_at"),
            sla_due_at=_required_text(payload.get("sla_due_at"), name="sla_due_at"),
            expires_at=_required_text(payload.get("expires_at"), name="expires_at"),
            reviewed_at=_optional_text(payload.get("reviewed_at")),
            reviewer=_optional_text(payload.get("reviewer")),
            resolution=_optional_text(payload.get("resolution")),
            expired_at=_optional_text(payload.get("expired_at")),
        )


@dataclass(frozen=True)
class ReviewQueueMetrics:
    backlog_size: int
    pending_count: int
    reviewed_count: int
    expired_count: int
    overdue_sla_count: int
    sla_days: int
    expiry_days: int
    evaluated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backlog_size": int(self.backlog_size),
            "pending_count": int(self.pending_count),
            "reviewed_count": int(self.reviewed_count),
            "expired_count": int(self.expired_count),
            "overdue_sla_count": int(self.overdue_sla_count),
            "sla_days": int(self.sla_days),
            "expiry_days": int(self.expiry_days),
            "evaluated_at": self.evaluated_at,
        }


class ReviewQueueService:
    """
    File-backed REVIEW queue with weekly SLA + configurable expiry.

    Layout:
    - <root>/pending/<review_id>.json
    - <root>/reviewed/<review_id>.json
    - <root>/expired/<review_id>.json
    """

    def __init__(
        self,
        *,
        path: str | Path | None = None,
        root: str | Path = DEFAULT_REVIEW_QUEUE_ROOT,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else DEFAULT_REVIEW_QUEUE_PATH
        self._state = load_review_queue(self._path)
        self._root = Path(root)
        self._env_override = env

    @property
    def state(self) -> ReviewQueueState:
        return self._state

    @property
    def root(self) -> Path:
        return self._root

    def reload(self) -> ReviewQueueState:
        self._state = load_review_queue(self._path)
        return self._state

    def enqueue(
        self,
        request: ReviewQueueRequest | Mapping[str, Any],
        *,
        now_ts: float | None = None,
    ) -> ReviewQueueEntry:
        now = _coerce_now_ts(now_ts)
        self.expire_due(now_ts=now)
        req = _to_request(request)
        review_id = req.review_id or _derived_review_id(req)
        existing = self.get(review_id, auto_expire=False)
        if existing is not None:
            return existing

        created_at = _iso_utc(now)
        sla_due_at = _iso_utc(now + (self._sla_days() * 24.0 * 3600.0))
        expires_at = _iso_utc(now + (self._expiry_days() * 24.0 * 3600.0))
        entry = ReviewQueueEntry(
            review_id=review_id,
            status="pending",
            subject_id=req.subject_id,
            reason=req.reason,
            payload=dict(req.payload),
            metadata=dict(req.metadata),
            source=req.source,
            created_at=created_at,
            sla_due_at=sla_due_at,
            expires_at=expires_at,
            reviewed_at=None,
            reviewer=None,
            resolution=None,
            expired_at=None,
        )
        self._write_entry(entry, status="pending")
        return entry

    def resolve(
        self,
        review_id: str,
        *,
        reviewer: str,
        resolution: str,
        notes: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        now_ts: float | None = None,
    ) -> ReviewQueueEntry:
        now = _coerce_now_ts(now_ts)
        self.expire_due(now_ts=now)
        current = self.get(review_id, auto_expire=False)
        if current is None:
            raise ReviewQueueNotFoundError(f"review queue entry not found: {review_id}")
        if current.status != "pending":
            return current

        merged_metadata = dict(current.metadata)
        extra_meta = _as_dict(metadata)
        merged_metadata.update(extra_meta)
        note_text = _optional_text(notes)
        if note_text:
            merged_metadata["resolution_notes"] = note_text

        updated = ReviewQueueEntry(
            review_id=current.review_id,
            status="reviewed",
            subject_id=current.subject_id,
            reason=current.reason,
            payload=dict(current.payload),
            metadata=merged_metadata,
            source=current.source,
            created_at=current.created_at,
            sla_due_at=current.sla_due_at,
            expires_at=current.expires_at,
            reviewed_at=_iso_utc(now),
            reviewer=_required_text(reviewer, name="reviewer"),
            resolution=_required_text(resolution, name="resolution"),
            expired_at=None,
        )
        self._write_entry(updated, status="reviewed")
        self._delete_if_exists(self._status_path("pending", current.review_id))
        return updated

    def get(
        self,
        review_id: str,
        *,
        auto_expire: bool = True,
        now_ts: float | None = None,
    ) -> ReviewQueueEntry | None:
        key = _required_text(review_id, name="review_id")
        if auto_expire:
            self.expire_due(now_ts=now_ts)
        for status in _KNOWN_STATUSES:
            path = self._status_path(status, key)
            if not path.exists():
                continue
            entry = self._read_entry(path)
            if entry is not None:
                return entry
        return None

    def list_pending(self, *, now_ts: float | None = None) -> List[ReviewQueueEntry]:
        now = _coerce_now_ts(now_ts)
        self.expire_due(now_ts=now)
        entries = self._list_status("pending")
        return sorted(entries, key=lambda item: (_parse_iso_ts(item.sla_due_at), item.review_id))

    def expire_due(self, *, now_ts: float | None = None) -> List[ReviewQueueEntry]:
        now = _coerce_now_ts(now_ts)
        pending = self._list_status("pending")
        expired: List[ReviewQueueEntry] = []
        for entry in pending:
            if _parse_iso_ts(entry.expires_at) > now:
                continue
            expired_entry = ReviewQueueEntry(
                review_id=entry.review_id,
                status="expired",
                subject_id=entry.subject_id,
                reason=entry.reason,
                payload=dict(entry.payload),
                metadata={**entry.metadata, "expired_reason": "sla_queue_ttl_elapsed"},
                source=entry.source,
                created_at=entry.created_at,
                sla_due_at=entry.sla_due_at,
                expires_at=entry.expires_at,
                reviewed_at=entry.reviewed_at,
                reviewer=entry.reviewer,
                resolution=entry.resolution,
                expired_at=_iso_utc(now),
            )
            self._write_entry(expired_entry, status="expired")
            self._delete_if_exists(self._status_path("pending", entry.review_id))
            expired.append(expired_entry)
        return sorted(expired, key=lambda item: item.review_id)

    def metrics(self, *, now_ts: float | None = None) -> ReviewQueueMetrics:
        now = _coerce_now_ts(now_ts)
        self.expire_due(now_ts=now)
        pending = self._list_status("pending")
        reviewed = self._list_status("reviewed")
        expired = self._list_status("expired")
        overdue_sla = 0
        for item in pending:
            if _parse_iso_ts(item.sla_due_at) <= now:
                overdue_sla += 1
        return ReviewQueueMetrics(
            backlog_size=len(pending),
            pending_count=len(pending),
            reviewed_count=len(reviewed),
            expired_count=len(expired),
            overdue_sla_count=overdue_sla,
            sla_days=self._sla_days(),
            expiry_days=self._expiry_days(),
            evaluated_at=_iso_utc(now),
        )

    def _sla_days(self) -> int:
        env_value = self._env().get("REVIEW_QUEUE_SLA_DAYS")
        return max(1, _safe_int(env_value, default=self._state.sla_days))

    def _expiry_days(self) -> int:
        env_value = self._env().get("REVIEW_QUEUE_EXPIRY_DAYS")
        return max(1, _safe_int(env_value, default=self._state.expiry_days))

    def _env(self) -> Mapping[str, str]:
        if self._env_override is not None:
            return self._env_override
        return os.environ

    def _list_status(self, status: str) -> List[ReviewQueueEntry]:
        if status not in _KNOWN_STATUSES:
            raise ValueError(f"unknown review queue status: {status}")
        root = self._status_dir(status)
        if not root.exists():
            return []
        out: List[ReviewQueueEntry] = []
        for path in sorted(root.glob("*.json"), key=lambda p: p.name):
            entry = self._read_entry(path)
            if entry is not None:
                out.append(entry)
        return out

    def _read_entry(self, path: Path) -> ReviewQueueEntry | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, Mapping):
            return None
        try:
            return ReviewQueueEntry.from_dict(payload)
        except Exception:
            return None

    def _write_entry(self, entry: ReviewQueueEntry, *, status: str) -> None:
        path = self._status_path(status, entry.review_id)
        self._write_json_atomic(path, entry.to_dict())

    def _write_json_atomic(self, path: Path, payload: Mapping[str, Any]) -> None:
        constitution = get_constitution_service()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
        constitution.require("write_path", {"path": path})
        constitution.require("write_path", {"path": tmp})
        try:
            tmp.write_text(canonical_json(dict(payload)) + "\n", encoding="utf-8")
            os.replace(str(tmp), str(path))
        finally:
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass

    def _delete_if_exists(self, path: Path) -> None:
        if not path.exists():
            return
        constitution = get_constitution_service()
        constitution.require("write_path", {"path": path})
        path.unlink(missing_ok=True)

    def _status_dir(self, status: str) -> Path:
        return self._root / status

    def _status_path(self, status: str, review_id: str) -> Path:
        return self._status_dir(status) / f"{safe_segment(review_id)}.json"


_REVIEW_QUEUE_SERVICE: ReviewQueueService | None = None


def get_review_queue_service(
    *,
    path: str | Path | None = None,
    root: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    reload: bool = False,
) -> ReviewQueueService:
    global _REVIEW_QUEUE_SERVICE
    if _REVIEW_QUEUE_SERVICE is None or path is not None or root is not None or env is not None:
        _REVIEW_QUEUE_SERVICE = ReviewQueueService(
            path=path,
            root=(root if root is not None else DEFAULT_REVIEW_QUEUE_ROOT),
            env=env,
        )
        return _REVIEW_QUEUE_SERVICE
    if reload:
        _REVIEW_QUEUE_SERVICE.reload()
    return _REVIEW_QUEUE_SERVICE


def load_review_queue(path: str | Path) -> ReviewQueueState:
    target = Path(path).resolve()
    payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        payload = {}
    defaults = payload.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
    schema_version = _safe_int(payload.get("schema_version"), default=1)
    version = str(payload.get("version") or "unknown")
    sla_days = max(1, _safe_int(defaults.get("sla_days"), default=7))
    expiry_days = max(1, _safe_int(defaults.get("expiry_days"), default=30))
    return ReviewQueueState(
        path=target,
        schema_version=schema_version,
        version=version,
        sla_days=sla_days,
        expiry_days=expiry_days,
    )


def _to_request(value: ReviewQueueRequest | Mapping[str, Any]) -> ReviewQueueRequest:
    if isinstance(value, ReviewQueueRequest):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("review queue request must be an object")
    return ReviewQueueRequest(
        review_id=_optional_text(value.get("review_id")),
        subject_id=_required_text(value.get("subject_id"), name="subject_id"),
        reason=_required_text(value.get("reason"), name="reason"),
        payload=_as_dict(value.get("payload")),
        metadata=_as_dict(value.get("metadata")),
        source=_optional_text(value.get("source")),
    )


def _derived_review_id(request: ReviewQueueRequest) -> str:
    digest = sha256_json(
        {
            "subject_id": request.subject_id,
            "reason": request.reason,
            "payload": request.payload,
            "metadata": request.metadata,
            "source": request.source,
        }
    )[:16]
    return f"review-{digest}"


def _coerce_now_ts(value: float | None) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return float(time.time())


def _iso_utc(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_ts(value: str) -> float:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _required_text(value: Any, *, name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _safe_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(k): v for k, v in value.items()}
    return {}

