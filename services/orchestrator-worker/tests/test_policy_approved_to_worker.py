from __future__ import annotations

import json
from typing import Any

import babyai_shared.core.orchestrator as core_orchestrator
from orchestrator_worker import OrchestratorWorker


class _FakeEventBus:
    def __init__(self) -> None:
        self.config = {
            "topics": {
                "decision_lifecycle": "decision.lifecycle",
                "decision_approval": "decision.approval",
                "policy_approved": "policy.approved",
                "decision_lifecycle_dlq": "decision.lifecycle.dlq",
            },
            "consumer": {"retry_max_attempts": 3, "retry_backoff_seconds": 0},
            "dedupe": {"running_ttl_seconds": 30, "final_ttl_seconds": 300},
        }

    def publish(self, **_: Any) -> None:
        return


class _FakeRedis:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[int, str]] = {}

    def setex(self, key: str, ttl_seconds: int, value: str) -> bool:
        self.rows[str(key)] = (int(ttl_seconds), str(value))
        return True

    def get(self, key: str) -> str | None:
        row = self.rows.get(str(key))
        if row is None:
            return None
        return row[1]


def test_policy_approved_event_is_cached_in_worker_redis() -> None:
    worker = OrchestratorWorker(event_bus=_FakeEventBus())  # type: ignore[arg-type]
    fake_redis = _FakeRedis()
    worker._policy_cache_redis = fake_redis

    worker._handle_policy_approved_event(
        event={
            "session_id": "sess-1",
            "domain_name": "medical-advice",
            "fingerprint": "a" * 64,
            "approved_at": "2026-03-01T10:00:00Z",
            "effective_policy": {
                "domain_name": "medical-advice",
                "write_scope": {"type": "policy_service"},
                "model_profile": "code",
            },
        }
    )

    key = "policy_bootstrap:effective_policy:medical-advice"
    assert key in fake_redis.rows
    ttl_seconds, payload_raw = fake_redis.rows[key]
    assert ttl_seconds == 604800
    payload = json.loads(payload_raw)
    assert payload["fingerprint"] == "a" * 64
    assert payload["effective_policy"]["model_profile"] == "code"


def test_core_orchestrator_uses_cached_effective_policy_before_legacy_fallback(monkeypatch) -> None:
    fake_redis = _FakeRedis()
    fake_redis.setex(
        "policy_bootstrap:effective_policy:housing-law",
        604800,
        json.dumps(
            {
                "effective_policy": {
                    "domain_name": "housing-law",
                    "write_scope": {"type": "policy_service"},
                    "model_profile": "reasoning",
                },
                "fingerprint": "b" * 64,
            }
        ),
    )

    monkeypatch.setattr(core_orchestrator, "_POLICY_CACHE_REDIS_CLIENT", fake_redis)
    monkeypatch.setattr(core_orchestrator, "_POLICY_CACHE_REDIS_FAILED", False)

    resolved = core_orchestrator._resolve_effective_policy(  # type: ignore[attr-defined]
        task={"domain_name": "housing-law"},
        knobs={},
    )
    assert resolved["model_profile"] == "reasoning"
    assert (resolved.get("write_scope") or {}).get("type") == "policy_service"
