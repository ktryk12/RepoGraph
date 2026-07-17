"""
tools/analytics_collector.py — Skill: passive analytics collection for published content.

Pure function skill — no side effects beyond appending to a JSON-lines log.
Called by ContentOrchestratorAgent after CONTENT_PUBLISHED signal.

Collects:
  - Platform confirmation metadata from publish signal
  - Engagement snapshot (likes, views) — if platform API available
  - Writes to logs/content_analytics.log (JSON lines)

Never raises. Always returns dict.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)

_LOG_DIR  = Path(os.getenv("BABYAI_LOG_DIR", "logs"))
_ANALYTICS_LOG = _LOG_DIR / "content_analytics.log"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_publish_event(publish_signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Record a CONTENT_PUBLISHED signal to the analytics log.

    Args:
        publish_signal: payload from content.published topic

    Returns:
        {
            "recorded": bool,
            "log_path": str,
            "entry_id": str,
        }
    """
    entry_id = publish_signal.get("publish_id", "unknown")
    entry = {
        "event":        "content_published",
        "recorded_at":  datetime.now(timezone.utc).isoformat(),
        "entry_id":     entry_id,
        "topic":        publish_signal.get("topic", ""),
        "channel":      publish_signal.get("channel", ""),
        "platform_ref": publish_signal.get("platform_ref", ""),
        "brief_id":     publish_signal.get("brief_id", ""),
        "video_ref":    publish_signal.get("video_ref", ""),
        "opportunity_score": publish_signal.get("opportunity_score", 0.0),
    }

    recorded = _append_log(entry)
    return {
        "recorded": recorded,
        "log_path": str(_ANALYTICS_LOG),
        "entry_id": entry_id,
    }


def record_failure_event(failure_signal: Dict[str, Any]) -> Dict[str, Any]:
    """Record a CONTENT_PUBLISH_FAILED signal."""
    entry_id = failure_signal.get("publish_id", "unknown")
    entry = {
        "event":       "content_publish_failed",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "entry_id":    entry_id,
        "channel":     failure_signal.get("channel", ""),
        "error":       failure_signal.get("error", ""),
        "brief_id":    failure_signal.get("brief_id", ""),
    }

    recorded = _append_log(entry)
    return {
        "recorded": recorded,
        "log_path": str(_ANALYTICS_LOG),
        "entry_id": entry_id,
    }


def read_recent_events(limit: int = 50) -> List[Dict[str, Any]]:
    """
    Read recent analytics events from log.

    Returns list of event dicts, most recent last.
    Never raises.
    """
    try:
        if not _ANALYTICS_LOG.exists():
            return []
        lines = _ANALYTICS_LOG.read_text(encoding="utf-8").splitlines()
        entries = []
        for line in lines[-limit:]:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return entries
    except Exception as exc:
        _log.debug("analytics_collector_read_failed error=%s", exc)
        return []


def summarize_channel_performance(channel: str) -> Dict[str, Any]:
    """
    Summarize publish success/failure counts for a channel.

    Returns:
        {"channel": str, "published": int, "failed": int, "total": int}
    """
    events = read_recent_events(limit=1000)
    published = sum(
        1 for e in events
        if e.get("event") == "content_published" and e.get("channel") == channel
    )
    failed = sum(
        1 for e in events
        if e.get("event") == "content_publish_failed" and e.get("channel") == channel
    )
    return {
        "channel":   channel,
        "published": published,
        "failed":    failed,
        "total":     published + failed,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _append_log(entry: Dict[str, Any]) -> bool:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=True, separators=(",", ":"))
        with _ANALYTICS_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return True
    except Exception as exc:
        _log.error("analytics_collector_write_failed error=%s", exc)
        return False
