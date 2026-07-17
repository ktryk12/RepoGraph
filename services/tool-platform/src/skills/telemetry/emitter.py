"""
skill_runtime/telemetry/emitter.py — Emit skill.execution.completed med fuld provenance.

Telemetry-schema er IKKE forhandlingsbar (jf. afsnit 4 i SKILL_RUNTIME_PROGRAM).
Alle felter skal være til stede — null hvis ikke udfyldbare.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)
_BROKERS   = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BOOTSTRAP", os.getenv("KAFKA_BROKERS", "kafka:9092")))
_TOPIC_OUT = "skill.execution.completed"
_TOPIC_FDB = "skill.user_feedback"


def _git_commit() -> Optional[str]:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=2,
        ).decode().strip()
    except Exception:
        return None


def _build_event(
    *,
    skill_name:     str,
    skill_version:  str,
    triggered_by:   str,
    trigger_context: Dict,
    user_prompt:    str,
    parameters:     Dict,
    context_pack:   Dict,
    prompts_used:   List[Dict],
    model_used:     str,
    raw_output:     str,
    structured_output: Dict,
    status:         str,
    artifacts_produced: List[str],
    findings_count: int,
    auto_fixes:     int,
    user_accepted:  Optional[bool],
    user_feedback:  Optional[str],
    policy_violations: List[str],
    duration_seconds: float,
    tokens_input:   int,
    tokens_output:  int,
    trace_id:       Optional[str] = None,
    related_artifact_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "event_id":   str(uuid.uuid4()),
        "timestamp":  now,
        "skill_name": skill_name,
        "skill_version": skill_version,
        "triggered_by":  triggered_by,
        "trigger_context": trigger_context,
        "input": {
            "user_prompt": user_prompt,
            "parameters":  parameters,
        },
        "context_pack": context_pack,
        "prompts_used": prompts_used,
        "model_used":   model_used,
        "output": {
            "raw":        raw_output[:2000],  # cap til 2K
            "structured": structured_output,
        },
        "outcome": {
            "status":               status,
            "artifacts_produced":   artifacts_produced,
            "findings_count":       findings_count,
            "auto_fixes_applied":   auto_fixes,
        },
        "quality_signals": {
            "user_accepted":     user_accepted,
            "user_feedback":     user_feedback,
            "policy_violations": policy_violations,
        },
        "metrics": {
            "duration_seconds": round(duration_seconds, 3),
            "tokens_input":     tokens_input,
            "tokens_output":    tokens_output,
            "cost_usd":         None,  # lokal model — ingen pris
        },
        "provenance": {
            "git_commit":           _git_commit(),
            "request_trace_id":     trace_id or str(uuid.uuid4()),
            "related_artifact_ids": related_artifact_ids or [],
        },
    }


def emit(event: Dict[str, Any]) -> bool:
    try:
        from confluent_kafka import Producer
        p   = Producer({"bootstrap.servers": _BROKERS})
        raw = json.dumps(event, ensure_ascii=True, separators=(",", ":")).encode()
        p.produce(topic=_TOPIC_OUT, key=event["event_id"].encode(), value=raw)
        p.flush(timeout=5)
        _log.info("telemetry_emitted skill=%s status=%s",
                  event.get("skill_name"), event.get("outcome", {}).get("status"))
        return True
    except Exception as exc:
        _log.warning("telemetry_emit_error error=%s", exc)
        return False


def emit_feedback(
    event_id: str, skill_name: str, accepted: bool, feedback: Optional[str] = None
) -> bool:
    try:
        from confluent_kafka import Producer
        p   = Producer({"bootstrap.servers": _BROKERS})
        raw = json.dumps({
            "event_id":    event_id,
            "skill_name":  skill_name,
            "accepted":    accepted,
            "feedback":    feedback,
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=True).encode()
        p.produce(topic=_TOPIC_FDB, key=event_id.encode(), value=raw)
        p.flush(timeout=5)
        return True
    except Exception as exc:
        _log.warning("telemetry_feedback_error error=%s", exc)
        return False


# Convenience builder
build_event = _build_event
