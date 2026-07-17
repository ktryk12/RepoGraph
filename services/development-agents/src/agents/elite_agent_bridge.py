from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen

# Event-driven microservice communication per ADR-0015
from babyai_schemas.event_schemas import SwarmEvent, EmergenceSignal
from babyai_bus import KafkaConsumer

_log = logging.getLogger(__name__)

# Mirrors SwarmOrchestrator._PEER_CONFIDENCE_THRESHOLD
_PEER_CONFIDENCE_THRESHOLD = 0.65


@dataclass
class TierDecision:
    signal: EmergenceSignal
    tier: int          # 0 = ignore, 2 = LLM analysis, 3 = Council escalation
    reason: str
    confidence: float
    timestamp: float


class EliteAgentBridge:
    """Routes EmergenceSignals to the correct processing tier.

    Tier 0 — ignored  (severity < 0.6)
    Tier 2 — LLM analysis via LlamaCppRunnerGateway (0.6 ≤ severity < 0.8)
    Tier 3 — full BabyAI Council pipeline (severity ≥ 0.8 / special types)

    Injected into SwarmRuntime via set_bridge().  Called from
    SwarmRuntime._detect_and_route() via asyncio.create_task().

    asyncio.Semaphore(5) ensures at most 5 concurrent LLM calls (CPU-only).
    """

    def __init__(
        self,
        model_runner_url: Optional[str] = None,
        tool_runtime_url: Optional[str] = None,
        memory_plane_url: Optional[str] = None,
        observer: Any = None,
    ) -> None:
        self._model_runner_url = (
            model_runner_url
            or os.environ.get("MODEL_RUNNER_URL", "http://host.docker.internal:8081")
        ).rstrip("/")
        self._tool_runtime_url = (
            tool_runtime_url
            or os.environ.get("TOOL_RUNTIME_URL", "http://host.docker.internal:8093")
        ).rstrip("/")
        self._memory_plane_url = (
            memory_plane_url
            or os.environ.get("MEMORY_PLANE_URL", "http://memory-plane:8101")
        ).rstrip("/")

        from aesa.infrastructure.model_runner_http import LlamaCppRunnerGateway
        self._gateway = LlamaCppRunnerGateway(
            base_url=self._model_runner_url,
            model_ref=os.environ.get("MODEL_REF", "mamba-gpt-7b-q2"),
            timeout_seconds=30.0,
        )
        self._llm_semaphore = asyncio.Semaphore(5)
        self.observer = observer

    # ------------------------------------------------------------------
    # Classification — pure rules, no I/O
    # ------------------------------------------------------------------

    def classify_signal(self, signal: EmergenceSignal) -> TierDecision:
        """Classify a signal into tier 0/2/3 using rule-based logic only."""
        t = time.time()

        # Tier 3 conditions
        if signal.signal_type == "policy_violation":
            return TierDecision(signal=signal, tier=3,
                                reason="policy_violation_always_tier3",
                                confidence=1.0, timestamp=t)
        if signal.severity >= 0.8:
            return TierDecision(signal=signal, tier=3,
                                reason=f"severity_{signal.severity:.2f}_gte_0.8",
                                confidence=0.9, timestamp=t)
        if signal.signal_type == "energy_collapse" and signal.severity >= 0.7:
            return TierDecision(signal=signal, tier=3,
                                reason="energy_collapse_severity_gte_0.7",
                                confidence=0.85, timestamp=t)

        # Tier 2 conditions
        if signal.severity >= 0.6:
            return TierDecision(signal=signal, tier=2,
                                reason=f"severity_{signal.severity:.2f}_in_0.6_0.8",
                                confidence=0.75, timestamp=t)
        if signal.signal_type in ("polarization", "low_confidence", "anomaly"):
            return TierDecision(signal=signal, tier=2,
                                reason=f"signal_type_{signal.signal_type}_tier2",
                                confidence=0.7, timestamp=t)

        # Tier 0 — ignore
        return TierDecision(signal=signal, tier=0,
                            reason=f"severity_{signal.severity:.2f}_lt_0.6",
                            confidence=1.0, timestamp=t)

    # ------------------------------------------------------------------
    # Main entry point — called via asyncio.create_task()
    # ------------------------------------------------------------------

    async def handle_signals(
        self,
        signals: List[EmergenceSignal],
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Route each signal to the appropriate tier."""
        tier2: List[TierDecision] = []
        tier3: List[TierDecision] = []
        ignored: List[TierDecision] = []

        for signal in signals:
            decision = self.classify_signal(signal)
            if decision.tier == 3:
                tier3.append(decision)
            elif decision.tier == 2:
                tier2.append(decision)
            else:
                ignored.append(decision)

        if tier2:
            await self._handle_tier2_batch(tier2, snapshot)
        for d in tier3:
            await self._handle_tier3(d, snapshot)

        return {
            "tier2_handled": len(tier2),
            "tier3_escalated": len(tier3),
            "ignored": len(ignored),
            "tick": signals[0].tick if signals else 0,
        }

    # ------------------------------------------------------------------
    # Tier 2 — batched LLM analysis
    # ------------------------------------------------------------------

    async def _handle_tier2_batch(
        self,
        decisions: List[TierDecision],
        snapshot: Dict[str, Any],
    ) -> List[Any]:
        async def analyse_one(decision: TierDecision) -> Dict[str, Any]:
            prompt = (
                f"Swarm signal: {decision.signal.signal_type}\n"
                f"Severity: {decision.signal.severity:.2f}\n"
                f"Payload: {decision.signal.payload}\n"
                f"Affected agents (sample):\n"
                f"{self._format_agent_sample(decision.signal.agent_ids, snapshot)}\n\n"
                "Analysér dette swarm-mønster:\n"
                "1. Hvad driver dette mønster?\n"
                "2. Er det stabilt eller eskalerende?\n"
                "3. Anbefaling (parameter_update/monitor/ignore)?\n\n"
                'Svar KUN i JSON:\n'
                '{"analysis": str, "stable": bool, "recommendation": str, "confidence": float}'
            )
            async with self._llm_semaphore:
                result = await self._call_llm_async(prompt, decision.signal)

            conf = float(result.get("confidence", 0.5))
            if conf < _PEER_CONFIDENCE_THRESHOLD:
                _log.info(
                    "Tier2 low-confidence signal_type=%s confidence=%.2f",
                    decision.signal.signal_type,
                    conf,
                )

            # S5 directive publishing hook
            if result.get("recommendation") == "parameter_update":
                try:
                    from policy.swarm_directive_publisher import PolicySwarmDirectivePublisher
                    publisher = PolicySwarmDirectivePublisher(kafka_publisher=None)
                    status = publisher.publish_from_recommendation(
                        recommendation="parameter_update",
                        signal_context={
                            "signal_type": decision.signal.signal_type,
                            "analysis": result.get("analysis", ""),
                            "severity": decision.signal.severity,
                            "payload": decision.signal.payload,
                        },
                        approved_by="elite_agent_bridge",
                    )
                    _log.info("Directive status: %s", status.get("status"))
                except Exception as exc:
                    _log.warning("publish_from_recommendation failed: %s", exc)

            self._ingest_to_memory(
                content=f"Tier2: {result.get('analysis', '')}",
                entity_type="pattern",
                entity_id=decision.signal.signal_type,
                importance=decision.signal.severity,
            )
            return result

        tasks = [analyse_one(d) for d in decisions]
        return await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Tier 3 — Council escalation
    # ------------------------------------------------------------------

    async def _handle_tier3(
        self,
        decision: TierDecision,
        snapshot: Dict[str, Any],
    ) -> None:
        # 1. Tool runtime assessment (fire-and-forget, fail-open)
        tool_result = self._call_tool_runtime(
            action="assess",
            payload={
                "signal": {
                    "signal_type": decision.signal.signal_type,
                    "severity": decision.signal.severity,
                    "tick": decision.signal.tick,
                    "payload": decision.signal.payload,
                    "agent_count": len(decision.signal.agent_ids),
                },
                "snapshot_sample": self._sample_snapshot(snapshot, n=10),
            },
        )

        # 2. Publish to decision.requested via observer (S2 path)
        if self.observer is not None:
            try:
                event = SwarmEvent.create(
                    event_type=f"tier3.{decision.signal.signal_type}",
                    agent_count=len(snapshot),
                    tick=decision.signal.tick,
                    payload={
                        "signal_type": decision.signal.signal_type,
                        "severity": decision.signal.severity,
                        "tool_result": tool_result,
                        "payload": decision.signal.payload,
                    },
                    severity=max(0, min(10, round(decision.signal.severity * 10))),
                )
                self.observer.publish_event(event)
            except Exception as exc:
                _log.warning("Tier3 publish_event failed: %s", exc)

        _log.info(
            "Tier3 escalation signal_type=%s severity=%.2f tick=%d",
            decision.signal.signal_type,
            decision.signal.severity,
            decision.signal.tick,
        )

    # ------------------------------------------------------------------
    # LLM helpers
    # ------------------------------------------------------------------

    async def _call_llm_async(
        self,
        prompt: str,
        signal: EmergenceSignal,
    ) -> Dict[str, Any]:
        """Async wrapper around synchronous LlamaCppRunnerGateway.generate()."""
        loop = asyncio.get_event_loop()
        decision_id = f"swarm-{signal.signal_type}-{signal.tick}"
        context_id = f"emergence-{signal.tick}"
        try:
            raw = await loop.run_in_executor(
                None,
                lambda: self._gateway.generate(
                    decision_id=decision_id,
                    context_id=context_id,
                    purpose="swarm_tier2_analysis",
                    prompt=prompt,
                    max_tokens=200,
                    temperature=0.3,
                ),
            )
            text = raw.get("text", "") if isinstance(raw, dict) else str(raw)
            return self._parse_llm_json(text)
        except Exception as exc:
            _log.warning("LLM call failed signal_type=%s: %s", signal.signal_type, exc)
            return {
                "analysis": "llm_error",
                "stable": True,
                "recommendation": "monitor",
                "confidence": 0.3,
            }

    def _parse_llm_json(self, raw: Any) -> Dict[str, Any]:
        """Robust JSON parsing of LLM output. Strips markdown fences."""
        defaults: Dict[str, Any] = {
            "analysis": "parse_error",
            "stable": True,
            "recommendation": "monitor",
            "confidence": 0.3,
        }
        text = str(raw or "").strip()
        if not text:
            return defaults

        # Strip markdown code fences
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
        text = text.strip()

        # Try to extract a JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)

        try:
            parsed = json.loads(text)
            if not isinstance(parsed, dict):
                return defaults
            return {
                "analysis": str(parsed.get("analysis", defaults["analysis"])),
                "stable": bool(parsed.get("stable", defaults["stable"])),
                "recommendation": str(parsed.get("recommendation", defaults["recommendation"])),
                "confidence": float(parsed.get("confidence", defaults["confidence"])),
            }
        except Exception:
            return defaults

    # ------------------------------------------------------------------
    # HTTP helpers — all fail-open
    # ------------------------------------------------------------------

    def _call_tool_runtime(
        self,
        action: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """POST to tool_runtime. Returns {} on any error — never raises."""
        try:
            body = json.dumps({"action": action, "payload": payload},
                              ensure_ascii=True).encode("utf-8")
            req = Request(
                url=f"{self._tool_runtime_url}/swarm/assess",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=5.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            _log.debug("tool_runtime call failed (fail-open): %s", exc)
            return {}

    def _ingest_to_memory(
        self,
        content: str,
        entity_type: str,
        entity_id: str,
        importance: float,
    ) -> None:
        """POST to memory-plane /memory/ingest. Logs error — never raises."""
        try:
            payload = {
                "content": str(content),
                "source": "elite_agent_bridge",
                "entity_type": str(entity_type),
                "entity_id": str(entity_id),
                "metadata": {},
                "importance": float(importance),
            }
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            req = Request(
                url=f"{self._memory_plane_url}/memory/ingest",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=5.0) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                _log.debug("memory ingest id=%s entity=%s", result.get("id"), entity_id)
        except Exception as exc:
            _log.error("memory ingest failed: %s", exc)

    # ------------------------------------------------------------------
    # Snapshot helpers
    # ------------------------------------------------------------------

    def _format_agent_sample(
        self,
        agent_ids: List[str],
        snapshot: Dict[str, Any],
        n: int = 5,
    ) -> str:
        """Format a sample of agent states for LLM prompt."""
        lines = []
        for aid in agent_ids[:n]:
            info = snapshot.get(aid, {})
            energy = info.get("energy", "?")
            opinion = info.get("opinion", "?")
            confidence = info.get("confidence", "?")
            lines.append(f"  {aid}: energy={energy} opinion={opinion} confidence={confidence}")
        return "\n".join(lines) if lines else "  (no agent data)"

    def _sample_snapshot(
        self,
        snapshot: Dict[str, Any],
        n: int = 10,
    ) -> Dict[str, Any]:
        """Return the n agents with lowest confidence — for Council context."""
        if not snapshot:
            return {}
        ranked = sorted(
            snapshot.items(),
            key=lambda kv: float(kv[1].get("confidence", 1.0)),
        )
        return dict(ranked[:n])
