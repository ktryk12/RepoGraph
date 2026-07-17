from __future__ import annotations

from typing import Any

import pytest

from request_gate import main as request_gate_main


class _FakeRuntime:
    def __init__(self) -> None:
        self.pending = {
            "dec-1": {
                "decision_id": "dec-1",
                "context_id": "ctx-1",
                "policy_preset": "restricted",
                "required_policy_fingerprint": "a" * 64,
                "explanation": "approval required",
                "created_at": "2026-01-01T00:00:00Z",
            }
        }
        self.published_events: list[Any] = []

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def health(self) -> dict[str, Any]:
        return {"ok": True, "service": "request-gate"}

    def list_pending_approvals(self, *, limit: int = 200) -> list[dict[str, Any]]:
        _ = limit
        return [dict(row) for row in self.pending.values()]

    def get_pending_approval(self, decision_id: str) -> dict[str, Any] | None:
        row = self.pending.get(str(decision_id))
        return dict(row) if isinstance(row, dict) else None

    def publish_approval(
        self,
        *,
        decision_id: str,
        approved: bool,
        approved_by: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        pending = self.pending.get(str(decision_id))
        if pending is None:
            raise KeyError(decision_id)
        event = request_gate_main.build_approval_event_from_pending(
            pending,
            approved=bool(approved),
            approved_by=str(approved_by),
            reason=reason,
        )
        self.published_events.append(event)
        self.pending.pop(str(decision_id), None)
        return {
            "decision_id": event.decision_id,
            "context_id": str(event.context_id or ""),
            "approved": bool(event.approved),
            "policy_fingerprint": str(event.policy_fingerprint),
            "approved_by": str(event.approved_by),
            "approved_at": str(event.approved_at),
            "reason": str(event.reason or ""),
        }


def test_build_approval_event_uses_required_policy_fingerprint() -> None:
    event = request_gate_main.build_approval_event_from_pending(
        {
            "decision_id": "dec-abc",
            "context_id": "ctx-abc",
            "required_policy_fingerprint": "b" * 64,
        },
        approved=True,
        approved_by="ui-test",
        reason="ok",
    )
    assert event.decision_id == "dec-abc"
    assert event.context_id == "ctx-abc"
    assert event.policy_fingerprint == "b" * 64
    assert event.approved is True


def test_approve_endpoint_publishes_with_pending_fingerprint() -> None:
    pytest.importorskip("fastapi")
    testclient = pytest.importorskip("fastapi.testclient")

    runtime = _FakeRuntime()
    app = request_gate_main.create_app(runtime=runtime)  # type: ignore[arg-type]

    with testclient.TestClient(app) as client:
        response = client.post(
            "/approvals/dec-1/approve",
            json={"approved_by": "unit-test", "reason": "manual approve"},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["decision_id"] == "dec-1"
    assert payload["policy_fingerprint"] == "a" * 64

    assert len(runtime.published_events) == 1
    event = runtime.published_events[0]
    assert event.policy_fingerprint == "a" * 64
    assert event.approved is True
