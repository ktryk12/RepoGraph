from __future__ import annotations

import json
import threading
import time
from typing import Any, Mapping


class RedisPendingApprovalStore:
    def __init__(
        self,
        *,
        redis_url: str | None,
        namespace: str = "request_gate:pending_approvals",
        ttl_seconds: int = 86400,
        allow_in_memory_fallback: bool = True,
    ) -> None:
        self._namespace = str(namespace).strip() or "request_gate:pending_approvals"
        self._ttl_seconds = max(60, int(ttl_seconds))
        self._allow_in_memory_fallback = bool(allow_in_memory_fallback)
        self._redis = self._connect(redis_url=redis_url)
        self._memory_lock = threading.Lock()
        self._memory: dict[str, tuple[float, dict[str, Any]]] = {}

    def upsert_pending(self, record: Mapping[str, Any]) -> None:
        normalized = _normalize_record(record)
        decision_id = str(normalized.get("decision_id") or "").strip()
        if not decision_id:
            raise ValueError("pending approval requires decision_id")
        if not str(normalized.get("required_policy_fingerprint") or "").strip():
            raise ValueError("pending approval requires required_policy_fingerprint")

        if self._redis is not None:
            key = self._record_key(decision_id)
            try:
                self._redis.set(key, json.dumps(normalized, ensure_ascii=True), ex=self._ttl_seconds)
                self._redis.sadd(self._index_key(), decision_id)
                self._redis.expire(self._index_key(), self._ttl_seconds)
                return
            except Exception:
                if not self._allow_in_memory_fallback:
                    raise

        if not self._allow_in_memory_fallback:
            raise RuntimeError("pending_approval_store_unavailable")
        with self._memory_lock:
            self._prune_memory_locked()
            self._memory[decision_id] = (time.time() + self._ttl_seconds, normalized)

    def get_pending(self, decision_id: str) -> dict[str, Any] | None:
        normalized_id = str(decision_id or "").strip()
        if not normalized_id:
            return None

        if self._redis is not None:
            try:
                raw = self._redis.get(self._record_key(normalized_id))
                if raw is not None:
                    parsed = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else str(raw))
                    if isinstance(parsed, dict):
                        return _normalize_record(parsed)
                self._redis.srem(self._index_key(), normalized_id)
                return None
            except Exception:
                if not self._allow_in_memory_fallback:
                    raise

        with self._memory_lock:
            self._prune_memory_locked()
            row = self._memory.get(normalized_id)
            if row is None:
                return None
            return dict(row[1])

    def list_pending(self, *, limit: int = 200) -> list[dict[str, Any]]:
        max_items = max(1, int(limit))
        if self._redis is not None:
            try:
                pending: list[dict[str, Any]] = []
                raw_ids = self._redis.smembers(self._index_key()) or set()
                decision_ids = sorted(
                    [self._decode_text(item) for item in raw_ids if self._decode_text(item)],
                    key=str,
                )
                for decision_id in decision_ids:
                    row = self.get_pending(decision_id)
                    if row is not None:
                        pending.append(row)
                pending.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
                return pending[:max_items]
            except Exception:
                if not self._allow_in_memory_fallback:
                    raise

        with self._memory_lock:
            self._prune_memory_locked()
            pending = [dict(record) for _, record in self._memory.values()]
        pending.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return pending[:max_items]

    def mark_processed(
        self,
        *,
        decision_id: str,
        status: str,
        processed_by: str | None = None,
        reason: str | None = None,
    ) -> bool:
        normalized_id = str(decision_id or "").strip()
        if not normalized_id:
            return False
        touched = False

        if self._redis is not None:
            try:
                deleted = int(self._redis.delete(self._record_key(normalized_id)) or 0)
                self._redis.srem(self._index_key(), normalized_id)
                touched = deleted > 0
            except Exception:
                if not self._allow_in_memory_fallback:
                    raise

        with self._memory_lock:
            self._prune_memory_locked()
            if self._memory.pop(normalized_id, None) is not None:
                touched = True

        _ = (status, processed_by, reason)
        return touched

    def backend(self) -> str:
        if self._redis is not None:
            return "redis"
        if self._allow_in_memory_fallback:
            return "memory"
        return "unavailable"

    def _record_key(self, decision_id: str) -> str:
        return f"{self._namespace}:record:{decision_id}"

    def _index_key(self) -> str:
        return f"{self._namespace}:index"

    def _prune_memory_locked(self) -> None:
        now = time.time()
        expired = [key for key, (expires_at, _) in self._memory.items() if expires_at <= now]
        for key in expired:
            self._memory.pop(key, None)

    @staticmethod
    def _decode_text(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace").strip()
        return str(value or "").strip()

    @staticmethod
    def _connect(*, redis_url: str | None) -> Any | None:
        raw_url = str(redis_url or "").strip()
        if not raw_url:
            return None
        try:
            import redis  # type: ignore
        except Exception:
            return None
        try:
            client = redis.Redis.from_url(raw_url)
            client.ping()
            return client
        except Exception:
            return None


def _normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(record or {})
    decision_id = str(payload.get("decision_id") or "").strip()
    context_id = str(payload.get("context_id") or "").strip()
    policy_preset = str(payload.get("policy_preset") or "").strip()
    required_fingerprint = str(payload.get("required_policy_fingerprint") or "").strip().lower()
    explanation = str(payload.get("explanation") or "").strip()
    created_at = str(payload.get("created_at") or "").strip()
    user_prompt_raw = payload.get("user_prompt")
    user_prompt = str(user_prompt_raw).strip() if isinstance(user_prompt_raw, str) else ""
    safety_profile = str(payload.get("safety_profile") or "").strip()
    write_scope = str(payload.get("write_scope") or "").strip()
    policy_explanation = payload.get("policy_explanation")
    if_you_change = payload.get("if_you_change")
    normalized = {
        "decision_id": decision_id,
        "context_id": context_id,
        "policy_preset": policy_preset,
        "required_policy_fingerprint": required_fingerprint,
        "explanation": explanation,
        "created_at": created_at,
    }
    if safety_profile:
        normalized["safety_profile"] = safety_profile
    if write_scope:
        normalized["write_scope"] = write_scope
    if isinstance(policy_explanation, Mapping):
        normalized["policy_explanation"] = dict(policy_explanation)
    if isinstance(if_you_change, list):
        normalized["if_you_change"] = [item for item in if_you_change if isinstance(item, Mapping)]
    if user_prompt:
        normalized["user_prompt"] = user_prompt
    return normalized
