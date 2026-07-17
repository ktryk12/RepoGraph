from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

try:
    from fastapi import APIRouter, HTTPException
except Exception:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
    HTTPException = Exception  # type: ignore[assignment]


def create_router(*, governance_agent: Any, event_store: Any) -> Any:
    if APIRouter is None:
        raise RuntimeError("FastAPI is required for approval_router")
    router = APIRouter(prefix="/security", tags=["security-governance"])

    @router.get("/advisories")
    async def list_advisories() -> Dict[str, Any]:
        advisories = governance_agent.list_pending_advisories()
        rows = [_to_payload(item) for item in advisories]
        return {"pending": rows}

    @router.post("/advisories/{advisory_id}/approve")
    async def approve(advisory_id: str) -> Dict[str, Any]:
        action = await governance_agent.approve(advisory_id)
        if action is None:
            raise HTTPException(status_code=404, detail="advisory_not_found")
        return {"approved": True, "action": _to_payload(action)}

    @router.post("/advisories/{advisory_id}/reject")
    async def reject(advisory_id: str) -> Dict[str, Any]:
        advisory = governance_agent.reject(advisory_id)
        if advisory is None:
            raise HTTPException(status_code=404, detail="advisory_not_found")
        return {"rejected": True, "advisory": _to_payload(advisory)}

    @router.get("/events/summary")
    async def summary(hours: int = 24) -> Dict[str, Any]:
        hours_safe = max(1, int(hours))
        since = datetime.now(timezone.utc) - timedelta(hours=hours_safe)
        events = await event_store.get_since(since)
        counts: Dict[str, int] = {}
        for event in events:
            event_type = getattr(event, "event_type", "")
            key = str(getattr(event_type, "value", event_type))
            counts[key] = counts.get(key, 0) + 1
        return {"hours": hours_safe, "total": len(events), "count_by_type": counts}

    return router


def _to_payload(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return dict(model.model_dump())
    if hasattr(model, "dict"):
        return dict(model.dict())
    return {}
