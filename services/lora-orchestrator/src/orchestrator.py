from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any

from babyai.security.event_store import EventStore, SecurityEvent, SecurityEventType

from .fetchers import LoRAFetcher
from .hot_reload import HotReloadError, LoRAHotReloader
from .models import AdapterCandidate, GapReport, LoRAFlowResult, SecurityScore
from .registry_loader import LoRARegistry, load_lora_registry
from .self_trainer import LoRASelfTrainer, SelfTrainingFailedError


class EventStoreUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class VoteDecision:
    passed: bool
    score: float
    votes: dict[str, bool]


class LoRAOrchestrator:
    MAX_EVALUATED_CANDIDATES = 3
    S6_TIMEOUT_SECONDS = 15.0

    def __init__(
        self,
        *,
        fetcher: LoRAFetcher | None = None,
        self_trainer: LoRASelfTrainer | None = None,
        event_store: EventStore,
        security_l6: Any | None = None,
        anomaly_detector: Any | None = None,
        trend_detector: Any | None = None,
        governance_agent: Any | None = None,
        voter: Any | None = None,
        registry: LoRARegistry | None = None,
        hot_reloader: LoRAHotReloader | None = None,
    ) -> None:
        self.fetcher = fetcher or LoRAFetcher()
        self.self_trainer = self_trainer or LoRASelfTrainer()
        self.event_store = event_store
        self.security_l6 = security_l6 or _NoopGate()
        self.anomaly_detector = anomaly_detector or _NoopGate()
        self.trend_detector = trend_detector or _NoopGate()
        self.governance_agent = governance_agent or _NoopGovernance()
        self.voter = voter
        self._registry = registry
        self._hot_reloader = hot_reloader

    async def run(self, gap: GapReport) -> LoRAFlowResult:
        if gap.severity == "low":
            await self._log_event("LORA_DEFERRED", {"gap_id": gap.gap_id, "reason": "low_severity"}, gap.domain, gap)
            return LoRAFlowResult(
                gap_id=gap.gap_id,
                outcome="deferred",
                adapter_id=None,
                security_score=0.0,
                votes={},
                warnings=[],
                next_evaluation=datetime.now(timezone.utc) + timedelta(days=7),
            )

        candidates = await self.fetcher.search(gap.domain)
        approved: list[tuple[AdapterCandidate, SecurityScore]] = []
        evaluated = 0
        for candidate in candidates:
            if evaluated >= self.MAX_EVALUATED_CANDIDATES:
                break
            evaluated += 1
            score = await self._evaluate_candidate(candidate, gap=gap)
            if score.s6_passed and score.s7_passed and score.s8_passed:
                approved.append((candidate, score))

        if not approved:
            return await self._self_train_flow(gap)

        selected, security_score = approved[0]
        decision = await self._vote(selected)
        if decision.passed:
            await self._log_event(
                "LORA_EXTERNAL_APPROVED",
                {"gap_id": gap.gap_id, "adapter_id": selected.candidate_id, "votes": decision.votes},
                gap.domain,
                gap,
            )
            await self._hot_reload(selected, gap)
            self._start_monitor(selected, gap)
            return LoRAFlowResult(
                gap_id=gap.gap_id,
                outcome="external_adapter",
                adapter_id=selected.candidate_id,
                security_score=security_score.overall_score,
                votes=decision.votes,
                warnings=[],
                next_evaluation=datetime.now(timezone.utc) + timedelta(days=30),
            )
        return await self._self_train_flow(gap)

    async def _evaluate_candidate(self, candidate: AdapterCandidate, *, gap: GapReport) -> SecurityScore:
        s6 = await self._run_l6(candidate)
        if not s6:
            await self._log_event("LORA_S6_FAIL", {"candidate": candidate.candidate_id}, gap.domain, gap)
            return SecurityScore(
                candidate_id=candidate.candidate_id,
                s6_passed=False,
                s7_passed=False,
                s8_passed=False,
                overall_score=0.0,
                disqualification_reason="s6_failed",
            )
        s7 = await self._run_gate(self.anomaly_detector, candidate)
        if not s7:
            await self._log_event("LORA_S7_FAIL", {"candidate": candidate.candidate_id}, gap.domain, gap)
            return SecurityScore(
                candidate_id=candidate.candidate_id,
                s6_passed=True,
                s7_passed=False,
                s8_passed=False,
                overall_score=0.33,
                disqualification_reason="s7_failed",
            )
        s8 = await self._run_gate(self.trend_detector, candidate)
        if not s8:
            await self._log_event("LORA_S8_FAIL", {"candidate": candidate.candidate_id}, gap.domain, gap)
            return SecurityScore(
                candidate_id=candidate.candidate_id,
                s6_passed=True,
                s7_passed=True,
                s8_passed=False,
                overall_score=0.66,
                disqualification_reason="s8_failed",
            )
        return SecurityScore(
            candidate_id=candidate.candidate_id,
            s6_passed=True,
            s7_passed=True,
            s8_passed=True,
            overall_score=1.0,
            disqualification_reason=None,
        )

    async def _self_train_flow(self, gap: GapReport) -> LoRAFlowResult:
        examples = await self.fetcher.collect_examples(gap.domain)
        try:
            adapter = await self.self_trainer.train(gap.domain, list(examples)[:500])
        except SelfTrainingFailedError:
            await self._log_event("LORA_SELFTRAIN_FAILED", {"gap_id": gap.gap_id}, gap.domain, gap)
            return LoRAFlowResult(
                gap_id=gap.gap_id,
                outcome="deferred",
                adapter_id=None,
                security_score=0.0,
                votes={},
                warnings=["self_training_failed"],
                next_evaluation=datetime.now(timezone.utc) + timedelta(days=7),
            )

        score = await self._evaluate_candidate(adapter, gap=gap)
        if score.s6_passed and score.s7_passed and score.s8_passed:
            await self._log_event(
                "LORA_SELFTRAIN_APPROVED",
                {"gap_id": gap.gap_id, "adapter_id": adapter.candidate_id},
                gap.domain,
                gap,
            )
            await self._hot_reload(adapter, gap)
            self._start_monitor(adapter, gap)
            return LoRAFlowResult(
                gap_id=gap.gap_id,
                outcome="self_trained",
                adapter_id=adapter.candidate_id,
                security_score=score.overall_score,
                votes={},
                warnings=[],
                next_evaluation=datetime.now(timezone.utc) + timedelta(days=30),
            )

        await self._log_event(
            "LORA_SELFTRAIN_REJECTED",
            {"gap_id": gap.gap_id, "adapter_id": adapter.candidate_id},
            gap.domain,
            gap,
        )
        return LoRAFlowResult(
            gap_id=gap.gap_id,
            outcome="deferred",
            adapter_id=None,
            security_score=score.overall_score,
            votes={},
            warnings=["self_training_security_rejected"],
            next_evaluation=datetime.now(timezone.utc) + timedelta(days=7),
        )

    async def _vote(self, adapter: AdapterCandidate) -> VoteDecision:
        if self.voter is None:
            return VoteDecision(passed=True, score=1.0, votes={})
        vote = self.voter(adapter)
        if hasattr(vote, "__await__"):
            vote = await vote
        if isinstance(vote, VoteDecision):
            return vote
        if isinstance(vote, dict):
            return VoteDecision(
                passed=bool(vote.get("passed")),
                score=float(vote.get("score", 0.0)),
                votes={str(k): bool(v) for k, v in dict(vote.get("votes", {})).items()},
            )
        return VoteDecision(passed=bool(vote), score=1.0 if bool(vote) else 0.0, votes={})

    async def _hot_reload(self, adapter: AdapterCandidate, gap: GapReport) -> None:
        registry = self._registry or _try_load_registry()
        hot_reloader = self._hot_reloader or _build_hot_reloader(registry, gap.domain)

        base_url = _resolve_base_url(registry, gap.domain)
        adapter_path = str(adapter.file_path)

        try:
            hot_reloader.reload(gap.domain, base_url, adapter_path)
        except HotReloadError:
            raise
        except Exception as exc:
            raise HotReloadError(gap.domain, f"hot_reload_unexpected: {exc}") from exc

        gov = getattr(self.governance_agent, "hot_reload", None)
        if callable(gov):
            result = gov(adapter.candidate_id, adapter.file_path, gap.domain)
            if hasattr(result, "__await__"):
                await result

    def _start_monitor(self, adapter: AdapterCandidate, gap: GapReport) -> None:
        monitor = getattr(self.governance_agent, "monitor", None)
        if not callable(monitor):
            return
        coro = monitor(adapter.candidate_id, gap.domain)
        if hasattr(coro, "__await__"):
            asyncio.create_task(coro)

    async def _run_l6(self, candidate: AdapterCandidate) -> bool:
        gate = self.security_l6
        run = getattr(gate, "run", None)
        if callable(run):
            result = run(candidate)
            if hasattr(result, "__await__"):
                try:
                    result = await asyncio.wait_for(result, timeout=self.S6_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    return False
            return _result_passed(result)
        return await self._run_gate(gate, candidate)

    async def _run_gate(self, gate: Any, candidate: AdapterCandidate) -> bool:
        for method_name in ("run", "evaluate", "check"):
            method = getattr(gate, method_name, None)
            if not callable(method):
                continue
            try:
                result = method(candidate)
                if hasattr(result, "__await__"):
                    result = await result
            except Exception:
                return False
            return _result_passed(result)
        return True

    async def _log_event(self, event_type: str, payload: dict[str, Any], domain: str, gap: GapReport) -> None:
        event = SecurityEvent(
            timestamp=datetime.now(timezone.utc),
            layer=7,
            event_type=SecurityEventType.TREND_FLAGGED,
            severity=_gap_severity_score(gap.severity),
            domain=str(domain),
            pattern=str(event_type),
            source="lora_orchestrator",
            raw_snippet=json.dumps(payload, ensure_ascii=True)[:2000],
            agent_ids=[],
        )
        try:
            await self.event_store.log(event)
        except EventStoreUnavailableError:
            raise
        except Exception as exc:
            raise EventStoreUnavailableError(str(exc))


def _try_load_registry() -> LoRARegistry | None:
    try:
        return load_lora_registry()
    except FileNotFoundError:
        return None


def _resolve_base_url(registry: LoRARegistry | None, domain: str) -> str:
    if registry is None:
        return "http://model-runner:8081"
    try:
        port = registry.get_port(domain)
        return f"http://model-runner:{port}"
    except KeyError:
        return "http://model-runner:8081"


def _build_hot_reloader(registry: LoRARegistry | None, domain: str) -> LoRAHotReloader:
    strategy = "restart"
    if registry is not None:
        strategy = str(registry.global_config.get("hot_reload_strategy", "restart"))
    return LoRAHotReloader(strategy=strategy)


class _NoopGate:
    async def run(self, _candidate: AdapterCandidate) -> dict[str, Any]:
        return {"passed": True}


class _NoopGovernance:
    async def hot_reload(self, _adapter_id: str, _file_path: Any, _domain: str) -> None:
        return None

    async def monitor(self, _adapter_id: str, _domain: str) -> None:
        return None


def _result_passed(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, dict):
        if "passed" in value:
            return bool(value["passed"])
        if "ok" in value:
            return bool(value["ok"])
    return bool(value)


def _gap_severity_score(severity: str) -> float:
    mapping = {"low": 0.33, "medium": 0.66, "high": 0.95}
    return float(mapping.get(str(severity).lower(), 0.66))
