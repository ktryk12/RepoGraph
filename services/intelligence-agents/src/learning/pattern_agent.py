from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any


@dataclass(frozen=True)
class Pattern:
    feature_combo: dict[str, Any]
    outcome: str
    hit_rate: float
    sample_size: int
    confidence: float
    discovered_at: str
    expires_at: str


class PatternAgent:
    def __init__(self, project_id: str, memory_ref: Any, min_sample_size: int = 30) -> None:
        clean_project_id = str(project_id or "").strip()
        if not clean_project_id:
            raise ValueError("project_id must be non-empty")
        self.project_id = clean_project_id
        self.memory_ref = memory_ref
        self.min_sample_size = max(1, int(min_sample_size))

    def analyze(self, time_window: Any) -> list[Pattern]:
        config = _normalize_time_window(time_window)
        domain = str(config.get("domain") or "learning").strip() or "learning"
        include_projects = _resolve_project_scope(self.project_id, config=config)
        self._enforce_cross_project_guardrails(include_projects=include_projects, config=config)

        rows = _load_event_rows(
            memory_ref=self.memory_ref,
            project_ids=include_projects,
            domain=config.get("domain"),
            since=config["since"],
        )
        samples = _extract_samples(rows)
        grouped: dict[str, dict[str, Any]] = {}
        cross_project_mode = len(set(include_projects)) > 1
        for sample in samples:
            feature_combo = dict(sample["feature_combo"])
            if cross_project_mode:
                feature_combo.pop("project_id", None)
            feature_key = _stable_json(feature_combo)
            row = grouped.setdefault(
                feature_key,
                {
                    "feature_combo": feature_combo,
                    "total": 0,
                    "outcomes": {},
                },
            )
            row["total"] = int(row["total"]) + 1
            outcome_key = str(sample["outcome"])
            row["outcomes"][outcome_key] = int(row["outcomes"].get(outcome_key, 0)) + 1

        discovered_at = _utc_now()
        expires_at = discovered_at + timedelta(days=float(config.get("ttl_days", 14.0)))
        out: list[Pattern] = []
        for payload in grouped.values():
            total = int(payload["total"])
            if total < self.min_sample_size:
                continue
            feature_combo = dict(payload["feature_combo"])
            for outcome, hits in dict(payload["outcomes"]).items():
                hit_rate = float(hits) / float(total)
                confidence = _pattern_confidence(
                    hit_rate=hit_rate,
                    sample_size=total,
                    min_sample_size=self.min_sample_size,
                )
                out.append(
                    Pattern(
                        feature_combo=feature_combo,
                        outcome=str(outcome),
                        hit_rate=round(hit_rate, 6),
                        sample_size=total,
                        confidence=round(confidence, 6),
                        discovered_at=_to_iso(discovered_at),
                        expires_at=_to_iso(expires_at),
                    )
                )
        out.sort(key=lambda item: (-item.confidence, -item.sample_size, str(item.outcome)))
        self._log_event(
            domain=domain,
            event_name="pattern_analysis_completed",
            payload={
                "min_sample_size": self.min_sample_size,
                "project_scope": include_projects,
                "window_since": _to_iso(config["since"]),
                "pattern_count": len(out),
                "patterns": [pattern.__dict__ for pattern in out],
            },
        )
        return out

    def _enforce_cross_project_guardrails(self, *, include_projects: list[str], config: dict[str, Any]) -> None:
        unique_projects = sorted(set(str(item) for item in include_projects if str(item).strip()))
        if len(unique_projects) <= 1:
            return
        profile = config.get("profile_config")
        allow_cross_project = False
        if isinstance(profile, dict):
            allow_cross_project = bool(profile.get("allow_cross_project_learning", False))
        if not allow_cross_project:
            raise PermissionError(
                "cross-project learning requires profile_config.allow_cross_project_learning=true"
            )

    def _log_event(self, *, domain: str, event_name: str, payload: dict[str, Any]) -> None:
        save = getattr(self.memory_ref, "save", None)
        if not callable(save):
            return
        save(
            self.project_id,
            str(domain or "learning"),
            "event",
            {
                "subtype": "learning_pattern_analysis",
                "learning_event": str(event_name),
                "project_id": self.project_id,
                "domain": str(domain or "learning"),
                "payload": dict(payload),
                "created_at": _to_iso(_utc_now()),
            },
        )


def _normalize_time_window(value: Any) -> dict[str, Any]:
    now = _utc_now()
    if isinstance(value, (int, float)):
        hours = max(0.001, float(value))
        return {"since": now - timedelta(hours=hours), "ttl_days": 14.0}
    if isinstance(value, str):
        text = str(value).strip()
        if text.endswith("h"):
            hours = max(0.001, float(text[:-1] or 24.0))
            return {"since": now - timedelta(hours=hours), "ttl_days": 14.0}
        if text.endswith("d"):
            days = max(0.001, float(text[:-1] or 7.0))
            return {"since": now - timedelta(days=days), "ttl_days": 14.0}
    if isinstance(value, dict):
        since = _parse_since(value.get("since"), now=now)
        hours = value.get("hours")
        days = value.get("days")
        if since is None and hours is not None:
            since = now - timedelta(hours=max(0.001, float(hours)))
        if since is None and days is not None:
            since = now - timedelta(days=max(0.001, float(days)))
        if since is None:
            since = now - timedelta(days=7.0)
        ttl_days = max(0.25, float(value.get("ttl_days", 14.0)))
        return {
            "since": since,
            "ttl_days": ttl_days,
            "domain": value.get("domain"),
            "include_projects": value.get("include_projects"),
            "profile_config": value.get("profile_config"),
        }
    return {"since": now - timedelta(days=7.0), "ttl_days": 14.0}


def _resolve_project_scope(project_id: str, *, config: dict[str, Any]) -> list[str]:
    include_projects = config.get("include_projects")
    if isinstance(include_projects, list):
        clean = [str(item).strip() for item in include_projects if str(item).strip()]
        if clean:
            return list(dict.fromkeys(clean))
    return [str(project_id)]


def _load_event_rows(
    *,
    memory_ref: Any,
    project_ids: list[str],
    domain: Any,
    since: datetime,
) -> list[dict[str, Any]]:
    db_path = getattr(memory_ref, "db_path", None)
    if db_path is None:
        return []
    path = Path(db_path).expanduser().resolve()
    if not path.exists():
        return []

    placeholders = ",".join("?" for _ in project_ids)
    params: list[Any] = [*project_ids, "event", _to_iso(since)]
    query = (
        f"""
        SELECT project_id, domain, content, created_at
        FROM memory_entries
        WHERE project_id IN ({placeholders})
          AND type = ?
          AND created_at >= ?
        """
    )
    if domain is not None and str(domain).strip():
        query += " AND domain = ?"
        params.append(str(domain).strip())
    query += " ORDER BY created_at ASC, id ASC"

    with sqlite3.connect(path.as_posix()) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, tuple(params)).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "project_id": str(row["project_id"]),
                "domain": str(row["domain"]),
                "created_at": str(row["created_at"]),
                "content": _loads_json(row["content"], fallback={}),
            }
        )
    return out


def _extract_samples(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        content = row.get("content")
        if not isinstance(content, dict):
            continue
        payload = content.get("payload")
        if not isinstance(payload, dict):
            payload = content

        votes = payload.get("votes")
        if isinstance(votes, list):
            for vote in votes:
                if not isinstance(vote, dict):
                    continue
                role = str(vote.get("role") or "unknown").strip().lower() or "unknown"
                outcome = str(vote.get("recommendation") or "unknown").strip().lower() or "unknown"
                confidence = _bucket_confidence(vote.get("confidence"))
                out.append(
                    {
                        "feature_combo": {
                            "project_id": str(row.get("project_id") or ""),
                            "agent_role": role,
                            "confidence_bucket": confidence,
                            "domain": str(row.get("domain") or ""),
                        },
                        "outcome": outcome,
                    }
                )

        decision = payload.get("decision")
        if isinstance(decision, dict):
            outcome = str(decision.get("recommendation") or "unknown").strip().lower()
            if outcome:
                out.append(
                    {
                        "feature_combo": {
                            "project_id": str(row.get("project_id") or ""),
                            "agent_role": "council",
                            "confidence_bucket": _bucket_confidence(decision.get("confidence")),
                            "domain": str(row.get("domain") or ""),
                        },
                        "outcome": outcome,
                    }
                )
    return out


def _bucket_confidence(value: Any) -> str:
    try:
        score = float(value)
    except Exception:
        score = 0.0
    score = max(0.0, min(1.0, score))
    if score >= 0.8:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def _pattern_confidence(*, hit_rate: float, sample_size: int, min_sample_size: int) -> float:
    scale = min(1.0, float(sample_size) / float(max(1, min_sample_size * 2)))
    conservative = 1.0 - (1.0 - float(hit_rate)) ** 2
    return max(0.0, min(1.0, conservative * scale))


def _loads_json(raw: Any, *, fallback: Any) -> Any:
    if raw is None:
        return fallback
    try:
        return json.loads(str(raw))
    except Exception:
        return fallback


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _parse_since(value: Any, *, now: datetime) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
