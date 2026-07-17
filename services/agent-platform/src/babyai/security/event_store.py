from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
import json
from pathlib import Path
import sqlite3
from typing import Any, List
from uuid import uuid4

from pydantic import BaseModel, Field


class SecurityEventType(Enum):
    INJECTION_DETECTED = "injection_detected"
    OUTPUT_INVALID = "output_invalid"
    RATIONALE_FLAGGED = "rationale_flagged"
    ANOMALY_VOTES = "anomaly_votes"
    TREND_FLAGGED = "trend_flagged"


class SecurityEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime
    layer: int
    event_type: SecurityEventType
    severity: float
    domain: str
    pattern: str = ""
    source: str = ""
    raw_snippet: str = ""
    agent_ids: List[str] = Field(default_factory=list)


class EventStore:
    def __init__(self, path: str | Path = "state/security_events.sqlite") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    async def log(self, event: SecurityEvent) -> None:
        payload = _event_to_payload(event)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO security_events (
                    event_id,
                    timestamp,
                    layer,
                    event_type,
                    severity,
                    domain,
                    pattern,
                    source,
                    raw_snippet,
                    agent_ids
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["event_id"],
                    payload["timestamp"],
                    payload["layer"],
                    payload["event_type"],
                    payload["severity"],
                    payload["domain"],
                    payload["pattern"],
                    payload["source"],
                    payload["raw_snippet"],
                    payload["agent_ids"],
                ),
            )

    async def get_recent(self, days: int) -> List[SecurityEvent]:
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=max(0, int(days)))
        return await self.get_since(since=since)

    async def get_since(self, since: datetime) -> List[SecurityEvent]:
        marker = _to_utc(since).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    event_id,
                    timestamp,
                    layer,
                    event_type,
                    severity,
                    domain,
                    pattern,
                    source,
                    raw_snippet,
                    agent_ids
                FROM security_events
                WHERE timestamp >= ?
                ORDER BY timestamp DESC
                """,
                (marker,),
            ).fetchall()
        return [_row_to_event(row) for row in rows]

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS security_events (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    layer INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    severity REAL NOT NULL,
                    domain TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    source TEXT NOT NULL,
                    raw_snippet TEXT NOT NULL,
                    agent_ids TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_security_events_timestamp ON security_events(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_security_events_event_type ON security_events(event_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_security_events_domain ON security_events(domain)")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


def _event_to_payload(event: SecurityEvent) -> dict[str, Any]:
    timestamp = _to_utc(event.timestamp).isoformat()
    return {
        "event_id": str(event.event_id),
        "timestamp": timestamp,
        "layer": int(event.layer),
        "event_type": str(event.event_type.value),
        "severity": float(event.severity),
        "domain": str(event.domain),
        "pattern": str(event.pattern),
        "source": str(event.source),
        "raw_snippet": str(event.raw_snippet),
        "agent_ids": json.dumps([str(item) for item in list(event.agent_ids)], ensure_ascii=True),
    }


def _row_to_event(row: sqlite3.Row) -> SecurityEvent:
    raw_agent_ids = row["agent_ids"]
    agent_ids: List[str] = []
    try:
        decoded = json.loads(str(raw_agent_ids or "[]"))
        if isinstance(decoded, list):
            agent_ids = [str(item) for item in decoded if str(item).strip()]
    except Exception:
        agent_ids = []
    payload = {
        "event_id": str(row["event_id"]),
        "timestamp": datetime.fromisoformat(str(row["timestamp"])),
        "layer": int(row["layer"]),
        "event_type": SecurityEventType(str(row["event_type"])),
        "severity": float(row["severity"]),
        "domain": str(row["domain"]),
        "pattern": str(row["pattern"]),
        "source": str(row["source"]),
        "raw_snippet": str(row["raw_snippet"]),
        "agent_ids": agent_ids,
    }
    if hasattr(SecurityEvent, "model_validate"):
        return SecurityEvent.model_validate(payload)
    return SecurityEvent.parse_obj(payload)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
