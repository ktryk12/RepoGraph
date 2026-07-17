from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import time
from typing import Any

from babyai.lora.models import GapReport
from babyai.lora.orchestrator import EventStoreUnavailableError, LoRAOrchestrator
from babyai.security.event_store import EventStore
from babyai.security.l7_governance.governance_agent import GovernanceAgent

logger = logging.getLogger(__name__)


def _build_redis_client() -> Any:
    redis_url = str(os.getenv("REDIS_URL", "")).strip()
    if not redis_url:
        raise RuntimeError("REDIS_URL is required for lora orchestrator")
    try:
        import redis  # type: ignore
    except Exception as exc:
        raise RuntimeError("redis package is required") from exc
    client = redis.Redis.from_url(redis_url)
    client.ping()
    return client


def _parse_gap(payload_raw: Any) -> GapReport | None:
    if isinstance(payload_raw, bytes):
        payload_raw = payload_raw.decode("utf-8", errors="replace")
    if not isinstance(payload_raw, str):
        return None
    try:
        payload = json.loads(payload_raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    gap_id = str(payload.get("gap_id") or "").strip()
    domain = str(payload.get("domain") or "").strip()
    severity = str(payload.get("severity") or "").strip().lower()
    if not gap_id or not domain or severity not in {"low", "medium", "high"}:
        return None
    evidence = payload.get("evidence")
    evidence_list = [str(row) for row in list(evidence or []) if str(row).strip()] if isinstance(evidence, list) else []
    return GapReport(
        gap_id=gap_id,
        domain=domain,
        severity=severity,  # type: ignore[arg-type]
        evidence=evidence_list,
    )


def main() -> int:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    redis_client = _build_redis_client()
    event_store = EventStore(path=os.getenv("SQLITE_PATH", "/app/artifacts/security_events.sqlite"))
    governance = GovernanceAgent(redis_client=redis_client)
    orchestrator = LoRAOrchestrator(
        event_store=event_store,
        governance_agent=governance,
    )

    gap_channel = str(os.getenv("LORA_GAP_CHANNEL", "babyai:lora_gaps")).strip()
    result_channel = str(os.getenv("LORA_RESULT_CHANNEL", "babyai:lora_results")).strip()
    pubsub = redis_client.pubsub()
    pubsub.subscribe(gap_channel)
    logger.info("lora_orchestrator_started gap_channel=%s result_channel=%s", gap_channel, result_channel)

    while True:
        msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if not msg:
            time.sleep(0.05)
            continue
        gap = _parse_gap(msg.get("data"))
        if gap is None:
            continue
        try:
            result = asyncio.run(orchestrator.run(gap))
        except EventStoreUnavailableError as exc:
            logger.error("lora_orchestrator_event_store_unavailable gap_id=%s error=%s", gap.gap_id, exc)
            continue
        payload = {
            "gap_id": result.gap_id,
            "outcome": result.outcome,
            "adapter_id": result.adapter_id,
            "security_score": float(result.security_score),
            "votes": dict(result.votes),
            "warnings": list(result.warnings),
            "next_evaluation": result.next_evaluation.astimezone(timezone.utc).isoformat(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        redis_client.publish(result_channel, json.dumps(payload, ensure_ascii=True))
        logger.info("lora_orchestrator_result gap_id=%s outcome=%s", result.gap_id, result.outcome)


if __name__ == "__main__":
    raise SystemExit(main())
