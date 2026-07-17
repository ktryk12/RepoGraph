from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Any, Literal


_DEFAULT_REQUIRED_ROLES = [
    "context_agent",
    "evidence_agent",
    "domain_expert",
    "risk_policy_agent",
    "counterargument_agent",
    "historian_agent",
    "planner_agent",
    "evaluator_agent",
]

_HIGH_SEVERITY_ROLES = {"risk_policy_agent", "evidence_agent", "domain_expert", "evaluator_agent"}


@dataclass(frozen=True)
class CapabilityGap:
    domain: str
    missing_role: str
    evidence: list[str]
    severity: Literal["low", "medium", "high"]


class GapDetector:
    def __init__(self, project_id: str, memory_ref: Any, lora_gap_channel: str = "babyai:lora_gaps") -> None:
        self.project_id = str(project_id or "").strip()
        if not self.project_id:
            raise ValueError("project_id must be non-empty")
        self.memory_ref = memory_ref
        self.lora_gap_channel = str(lora_gap_channel or "babyai:lora_gaps").strip() or "babyai:lora_gaps"

    def detect_capability_gaps(self, council: Any) -> list[CapabilityGap]:
        domain = str(getattr(council, "domain", "general") or "general").strip() or "general"
        present_roles = _present_roles(council)
        required_roles = _required_roles(council)

        gaps: list[CapabilityGap] = []
        for role in required_roles:
            if role in present_roles:
                continue
            severity = "high" if role in _HIGH_SEVERITY_ROLES else "medium"
            gaps.append(
                CapabilityGap(
                    domain=domain,
                    missing_role=role,
                    evidence=[f"role_missing:{role}", f"present_roles:{','.join(sorted(present_roles))}"],
                    severity=severity,
                )
            )

        channel_gaps = self._consume_lora_channel(domain=domain)
        gaps.extend(channel_gaps)
        deduped = _dedupe_gaps(gaps)
        self._log_event(
            domain=domain,
            event_name="capability_gaps_detected",
            payload={"council_id": str(getattr(council, "council_id", "")), "gaps": [gap.__dict__ for gap in deduped]},
        )
        return deduped

    def _consume_lora_channel(self, *, domain: str) -> list[CapabilityGap]:
        redis_client = getattr(self.memory_ref, "redis", None)
        messages = _read_channel_messages(redis_client=redis_client, channel=self.lora_gap_channel)
        out: list[CapabilityGap] = []
        for raw in messages:
            payload = _decode_payload(raw)
            msg_domain = str(payload.get("domain") or domain).strip() or domain
            if msg_domain != domain:
                continue
            missing_role = str(payload.get("missing_role") or "domain_expert").strip() or "domain_expert"
            severity = _normalize_severity(payload.get("severity"))
            evidence = payload.get("evidence")
            evidence_list = [str(item).strip() for item in list(evidence or []) if str(item).strip()]
            if not evidence_list:
                marker = str(payload.get("gap_id") or "lora_gap")
                evidence_list = [f"lora_gap_channel:{marker}"]
            out.append(
                CapabilityGap(
                    domain=msg_domain,
                    missing_role=missing_role,
                    evidence=evidence_list,
                    severity=severity,
                )
            )
        return out

    def _log_event(self, *, domain: str, event_name: str, payload: dict[str, Any]) -> None:
        save = getattr(self.memory_ref, "save", None)
        if not callable(save):
            return
        save(
            self.project_id,
            str(domain or "recruiter"),
            "event",
            {
                "recruiter_event": str(event_name),
                "project_id": self.project_id,
                "domain": str(domain or "recruiter"),
                "payload": dict(payload),
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
        )


def _present_roles(council: Any) -> set[str]:
    roster = list(getattr(council, "agent_roster", []) or [])
    out: set[str] = set()
    for agent in roster:
        role = str(getattr(agent, "role", "")).strip()
        if role:
            out.add(role)
    return out


def _required_roles(council: Any) -> list[str]:
    roster = list(getattr(council, "agent_roster", []) or [])
    if roster:
        profile = getattr(roster[0], "profile_config", None)
        if isinstance(profile, dict):
            values = profile.get("agent_roster")
            if isinstance(values, list):
                clean = [str(item).strip() for item in values if str(item).strip()]
                if clean:
                    return list(dict.fromkeys(clean))
    return list(_DEFAULT_REQUIRED_ROLES)


def _read_channel_messages(*, redis_client: Any, channel: str) -> list[Any]:
    if redis_client is None:
        return []

    consume = getattr(redis_client, "consume_channel", None)
    if callable(consume):
        try:
            rows = consume(str(channel))
            if isinstance(rows, list):
                return list(rows)
        except Exception:
            return []

    fetch = getattr(redis_client, "get_channel_messages", None)
    if callable(fetch):
        try:
            rows = fetch(str(channel))
            if isinstance(rows, list):
                return list(rows)
        except Exception:
            return []

    channels = getattr(redis_client, "channels", None)
    if isinstance(channels, dict):
        rows = channels.get(str(channel), [])
        if isinstance(rows, list):
            channels[str(channel)] = []
            return list(rows)
    return []


def _decode_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw or "")
    if not text.strip():
        return {}
    try:
        decoded = json.loads(text)
    except Exception:
        return {"raw": text}
    return dict(decoded) if isinstance(decoded, dict) else {"raw": text}


def _normalize_severity(value: Any) -> Literal["low", "medium", "high"]:
    text = str(value or "medium").strip().lower()
    if text not in {"low", "medium", "high"}:
        return "medium"
    return text  # type: ignore[return-value]


def _dedupe_gaps(gaps: list[CapabilityGap]) -> list[CapabilityGap]:
    seen: set[tuple[str, str]] = set()
    out: list[CapabilityGap] = []
    for gap in gaps:
        key = (str(gap.domain), str(gap.missing_role))
        if key in seen:
            continue
        seen.add(key)
        out.append(gap)
    return out
