from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any, Optional

from .actions import (
    Advisory,
    GovernanceViolationError,
    NormalizationAction,
    PolicyAction,
    SandboxAction,
    ThresholdAction,
)

logger = logging.getLogger(__name__)


class GovernanceAgent:
    THREAT_CHANNEL = "babyai:threat_intel"
    POLICY_UPDATES_CHANNEL = "babyai:policy_updates"

    def __init__(self, *, redis_client: Any, skill_registry: Any | None = None) -> None:
        self.redis = redis_client
        self.skill_registry = skill_registry
        self._advisories: dict[str, Advisory] = {}
        self._pending_actions: dict[str, PolicyAction] = {}

    async def start(self) -> None:
        pubsub_factory = getattr(self.redis, "pubsub", None)
        if not callable(pubsub_factory):
            return
        pubsub = pubsub_factory()
        if hasattr(pubsub, "__aenter__"):
            async with pubsub as ps:
                await self._listen(ps)
            return
        await self._listen(pubsub)

    async def _listen(self, pubsub: Any) -> None:
        subscribe = getattr(pubsub, "subscribe", None)
        if callable(subscribe):
            result = subscribe(self.THREAT_CHANNEL)
            if hasattr(result, "__await__"):
                await result
        listen = getattr(pubsub, "listen", None)
        if not callable(listen):
            return
        async for message in listen():
            payload = _message_to_mapping(message)
            if payload is None:
                continue
            await self.on_threat(payload)

    async def on_threat(self, threat: dict[str, Any]) -> PolicyAction | Advisory | None:
        action = self._decide(threat)
        if action is None:
            return None
        severity = _severity(threat)
        if severity > 0.95:
            await self._execute(action, auto_approved=True)
            return action
        advisory = self._queue_advisory(action=action, severity=severity)
        return advisory

    def _queue_advisory(self, *, action: PolicyAction, severity: float) -> Advisory:
        payload = _action_payload(action)
        advisory = Advisory(
            reason="awaiting_approval",
            action_payload=payload,
            threat_severity=float(severity),
        )
        self._advisories[advisory.action_id] = advisory
        self._pending_actions[advisory.action_id] = action
        logger.info(
            "security_event event_type=governance.advisory_queued advisory_id=%s action_type=%s severity=%.3f",
            advisory.action_id,
            action.type,
            float(severity),
        )
        return advisory

    def _decide(self, threat: dict[str, Any]) -> Optional[PolicyAction]:
        severity = _severity(threat)
        if severity < 0.55:
            return None
        action_name = str(threat.get("recommended_action") or "").strip().lower()
        if action_name == "tighten_threshold":
            current_value = _as_float(threat.get("current_value"), default=0.25)
            proposed = _as_float(threat.get("new_value"), default=current_value * 0.70)
            lower = current_value * (1.0 - 0.30)
            upper = current_value * (1.0 + 0.30)
            clamped = min(max(proposed, lower), upper)
            return ThresholdAction(
                target_layer=5,
                parameter="MAX_EXTREME_RATIO",
                current_value=float(current_value),
                new_value=float(clamped),
            )
        if action_name == "sandbox_skill":
            source_event = threat.get("source_event")
            source_mapping = source_event if isinstance(source_event, dict) else {}
            skill_id = str(source_mapping.get("source") or threat.get("skill_id") or "").strip()
            if not skill_id:
                return None
            return SandboxAction(skill_id=skill_id, sandbox_hours=24)
        if action_name == "normalization":
            new_pattern = str(threat.get("new_pattern") or "").strip()
            if not new_pattern:
                return None
            return NormalizationAction(new_pattern=new_pattern)
        return None

    async def _execute(self, action: PolicyAction, auto_approved: bool = False) -> None:
        action.auto_approved = bool(auto_approved)
        action.executed_at = datetime.now(timezone.utc)
        action.status = "executed"
        if isinstance(action, SandboxAction) and self.skill_registry is not None:
            set_sandboxed = getattr(self.skill_registry, "set_sandboxed", None)
            if callable(set_sandboxed):
                result = set_sandboxed(action.skill_id, int(action.sandbox_hours))
                if hasattr(result, "__await__"):
                    await result
        payload = _action_payload(action)
        publish = getattr(self.redis, "publish", None)
        if callable(publish):
            result = publish(self.POLICY_UPDATES_CHANNEL, json.dumps(payload, ensure_ascii=True))
            if hasattr(result, "__await__"):
                await result
        logger.info(
            "security_event event_type=governance.action_executed action_id=%s type=%s auto_approved=%s",
            action.action_id,
            action.type,
            bool(auto_approved),
        )

    def list_pending_advisories(self) -> list[Advisory]:
        return [adv for adv in self._advisories.values() if adv.status == "pending"]

    async def approve(self, advisory_id: str) -> PolicyAction | None:
        advisory = self._advisories.get(str(advisory_id))
        action = self._pending_actions.pop(str(advisory_id), None)
        if advisory is None or action is None:
            return None
        advisory.status = "approved"
        await self._execute(action, auto_approved=False)
        return action

    def reject(self, advisory_id: str) -> Advisory | None:
        advisory = self._advisories.get(str(advisory_id))
        if advisory is None:
            return None
        advisory.status = "rejected"
        self._pending_actions.pop(str(advisory_id), None)
        logger.info(
            "security_event event_type=governance.advisory_rejected advisory_id=%s",
            advisory_id,
        )
        return advisory

    async def hot_reload(self, adapter_id: str, file_path: Any, domain: str) -> None:
        payload = {
            "type": "normalization",
            "adapter_id": str(adapter_id),
            "file_path": str(file_path),
            "domain": str(domain),
            "new_pattern": rf"\badapter:{str(adapter_id)}\b",
        }
        publish = getattr(self.redis, "publish", None)
        if callable(publish):
            result = publish(self.POLICY_UPDATES_CHANNEL, json.dumps(payload, ensure_ascii=True))
            if hasattr(result, "__await__"):
                await result

    async def monitor(self, adapter_id: str, domain: str) -> None:
        logger.info(
            "security_event event_type=governance.adapter_monitor_started adapter_id=%s domain=%s",
            str(adapter_id),
            str(domain),
        )


def _severity(threat: dict[str, Any]) -> float:
    explicit = _as_float(threat.get("severity"), default=None)
    if explicit is not None:
        return _clip01(explicit)
    anomaly = _as_float(threat.get("anomaly_score"), default=None)
    temporal = threat.get("temporal_pattern")
    temporal_map = temporal if isinstance(temporal, dict) else {}
    temporal_sev = _as_float(temporal_map.get("severity"), default=None)
    values = [value for value in (anomaly, temporal_sev) if value is not None]
    if not values:
        return 0.0
    return _clip01(max(values))


def _action_payload(action: PolicyAction) -> dict[str, Any]:
    if hasattr(action, "model_dump_json"):
        try:
            return dict(json.loads(action.model_dump_json()))
        except Exception:
            pass
    if hasattr(action, "json"):
        try:
            return dict(json.loads(action.json()))
        except Exception:
            pass
    if hasattr(action, "model_dump"):
        return dict(action.model_dump())
    return dict(action.dict())


def _as_float(value: Any, *, default: float | None) -> float | None:
    try:
        return float(value)
    except Exception:
        return default


def _clip01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def _message_to_mapping(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    raw = message.get("data")
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return None
    try:
        payload = json.loads(raw)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload
