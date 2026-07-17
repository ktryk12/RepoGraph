from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from babyai.learning.pattern_agent import _load_event_rows


@dataclass(frozen=True)
class LearningReport:
    active_patterns: list[dict[str, Any]]
    weight_updates: list[dict[str, Any]]
    fast_paths_used: int
    patterns_expired: int
    top_performing_agents: list[str]
    bottom_performing_agents: list[str]


class ReportGenerator:
    def __init__(self, project_id: str, memory_ref: Any) -> None:
        clean_project_id = str(project_id or "").strip()
        if not clean_project_id:
            raise ValueError("project_id must be non-empty")
        self.project_id = clean_project_id
        self.memory_ref = memory_ref

    def weekly_report(self) -> LearningReport:
        since = datetime.now(timezone.utc) - timedelta(days=7.0)
        rows = _load_event_rows(
            memory_ref=self.memory_ref,
            project_ids=[self.project_id],
            domain=None,
            since=since,
        )
        active_patterns: list[dict[str, Any]] = []
        weight_updates: list[dict[str, Any]] = []
        fast_paths_used = 0
        patterns_expired = 0
        agent_scores: dict[str, list[float]] = {}
        now = datetime.now(timezone.utc)

        for row in rows:
            content = row.get("content")
            if not isinstance(content, dict):
                continue
            subtype = str(content.get("subtype") or "")
            payload = content.get("payload")
            if not isinstance(payload, dict):
                payload = {}

            if subtype == "learning_pattern_analysis":
                patterns = payload.get("patterns")
                if isinstance(patterns, list):
                    for item in patterns:
                        if not isinstance(item, dict):
                            continue
                        expires_at = _parse_iso(item.get("expires_at"))
                        if expires_at is not None and expires_at > now:
                            active_patterns.append(dict(item))
                        elif expires_at is not None:
                            patterns_expired += 1
            elif subtype == "weight_update_applied":
                weight_updates.append(dict(payload))
            elif subtype == "fast_path_used":
                fast_paths_used += int(payload.get("count", 1) or 1)
            elif subtype == "pattern_expired":
                patterns_expired += int(payload.get("count", 1) or 1)

            role = str(payload.get("agent_role") or "").strip()
            if role:
                score = payload.get("performance")
                if score is None:
                    score = payload.get("confidence")
                try:
                    numeric = float(score)
                except Exception:
                    numeric = None
                if numeric is not None:
                    agent_scores.setdefault(role, []).append(numeric)

        top_agents, bottom_agents = _rank_agents(agent_scores)
        report = LearningReport(
            active_patterns=active_patterns,
            weight_updates=weight_updates,
            fast_paths_used=fast_paths_used,
            patterns_expired=patterns_expired,
            top_performing_agents=top_agents,
            bottom_performing_agents=bottom_agents,
        )
        self._log_report_event(report)
        return report

    def _log_report_event(self, report: LearningReport) -> None:
        save = getattr(self.memory_ref, "save", None)
        if not callable(save):
            return
        save(
            self.project_id,
            "learning",
            "event",
            {
                "subtype": "learning_report_generated",
                "project_id": self.project_id,
                "payload": {
                    "active_pattern_count": len(report.active_patterns),
                    "weight_update_count": len(report.weight_updates),
                    "fast_paths_used": int(report.fast_paths_used),
                    "patterns_expired": int(report.patterns_expired),
                },
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
        )


def _rank_agents(agent_scores: dict[str, list[float]]) -> tuple[list[str], list[str]]:
    averages: list[tuple[str, float]] = []
    for role, scores in agent_scores.items():
        if not scores:
            continue
        avg = sum(float(item) for item in scores) / float(len(scores))
        averages.append((role, avg))
    if not averages:
        return [], []
    averages.sort(key=lambda item: item[1], reverse=True)
    top = [role for role, _ in averages[:3]]
    bottom = [role for role, _ in sorted(averages, key=lambda item: item[1])[:3]]
    return top, bottom


def _parse_iso(value: Any) -> datetime | None:
    text = str(value or "").strip()
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
