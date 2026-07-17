"""
services/exercise_runner/runner.py — ExerciseRunner

Lifecycle (race-free):
  1. Map all topics through sandbox_topic() consistently
  2. arm() tracer — subscribes, blocks until partition assignment
  3. inject seed event (safe: consumer is already listening)
  4. collect() trace
  5. verify() result
  6. close tracer

All topic names flowing through inject AND observe are mapped through the
same sandbox_topic() call, so sandbox mode is fully consistent.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from shared.exercise.models import Scenario, ScenarioResult, Trace
from shared.exercise.hooks import (
    exercise_meta,
    is_live,
    live_mode_confirmed,
    map_topics,
    sandbox_topic,
)
from tracer import Tracer
from verifier import Verifier

_log = logging.getLogger("exercise-runner")


class ExerciseRunner:
    """
    Runs a single Scenario end-to-end and returns ScenarioResult.

    Args:
        mode    : "dry_run" | "sandbox" | "live"
        brokers : Kafka bootstrap servers
    """

    def __init__(self, mode: str, brokers: str) -> None:
        self._mode    = mode
        self._brokers = brokers

    def run(self, scenario: Scenario) -> ScenarioResult:
        # Hard gate for live mode
        if self._mode == "live" and not live_mode_confirmed():
            from shared.exercise.models import ScenarioResult, Trace
            return ScenarioResult(
                scenario=          scenario.name,
                exercise_id=       "none",
                verdict=           "CONFIG_ERROR",
                mode=              self._mode,
                trace=             Trace(exercise_id="none", scenario=scenario.name,
                                         started_at=datetime.now(timezone.utc)),
                missing_topics=    [],
                contract_failures= [],
                first_missing=     None,
                last_seen=         None,
                event_count=       0,
                error=             "live mode requires EXERCISE_CONFIRM=I_UNDERSTAND_THIS_IS_LIVE "
                                   "AND EXERCISE_ALLOW_EXTERNAL_WRITES=true",
                duration_ms=       0.0,
            )

        exercise_id    = f"ex-{uuid.uuid4().hex[:8]}"
        correlation_id = str(uuid.uuid4())

        # ── Map all topic names through sandbox_topic() consistently ──────────
        # Seed topic: where we inject
        mapped_seed = sandbox_topic(scenario.seed_topic)
        # Watch topics: what tracer subscribes to + verifier checks against
        mapped_watch = map_topics(scenario.expected_topics)

        _log.info(
            "exercise_start exercise_id=%s scenario=%s mode=%s seed=%s watch=%s",
            exercise_id, scenario.name, self._mode, mapped_seed, mapped_watch,
        )

        tracer  = Tracer(watch_topics=mapped_watch, brokers=self._brokers)
        started = time.monotonic()

        try:
            # Step 1: arm tracer BEFORE injection (race-free)
            tracer.arm(exercise_id)

            # Step 2: inject seed event (consumer already listening)
            self._inject(
                topic=         mapped_seed,
                payload=       scenario.seed_payload,
                exercise_id=   exercise_id,
                correlation_id=correlation_id,
            )

            # Step 3: collect
            trace = tracer.collect(
                exercise_id=     exercise_id,
                timeout_s=       scenario.timeout_s,
                expected_topics= mapped_watch,
            )
            trace.exercise_id = exercise_id
            trace.scenario    = scenario.name

        except RuntimeError as exc:
            # arm() timeout or Kafka unavailable
            _log.error("exercise_runner_error exercise_id=%s error=%s", exercise_id, exc)
            from shared.exercise.models import ScenarioResult, Trace
            duration_ms = (time.monotonic() - started) * 1000
            return ScenarioResult(
                scenario=          scenario.name,
                exercise_id=       exercise_id,
                verdict=           "FAIL",
                mode=              self._mode,
                trace=             Trace(exercise_id=exercise_id, scenario=scenario.name,
                                         started_at=datetime.now(timezone.utc)),
                missing_topics=    scenario.expected_topics,
                contract_failures= [],
                first_missing=     scenario.expected_topics[0] if scenario.expected_topics else None,
                last_seen=         None,
                event_count=       0,
                error=             str(exc),
                duration_ms=       duration_ms,
            )
        finally:
            tracer.close()

        duration_ms = (time.monotonic() - started) * 1000

        # Step 4: verify
        # Build a scenario view with sandbox-mapped topics for verifier
        mapped_scenario = _remap_scenario(scenario, mapped_watch)
        result = Verifier().verify(mapped_scenario, trace)
        result.duration_ms = duration_ms
        result.mode        = self._mode

        _log.info(
            "exercise_%s exercise_id=%s scenario=%s duration_ms=%.0f missing=%s",
            result.verdict.lower(), exercise_id, scenario.name,
            duration_ms, result.first_missing,
        )
        return result

    def _inject(
        self,
        topic:          str,
        payload:        Dict[str, Any],
        exercise_id:    str,
        correlation_id: str,
    ) -> None:
        try:
            from confluent_kafka import Producer
        except ImportError as exc:
            raise RuntimeError("confluent_kafka not available") from exc

        producer = Producer({"bootstrap.servers": self._brokers, "acks": "all"})
        full_payload = {
            **payload,
            **exercise_meta(exercise_id, correlation_id),
        }
        raw = json.dumps(full_payload, ensure_ascii=True, separators=(",", ":")).encode()
        producer.produce(topic=topic, key=exercise_id.encode(), value=raw)
        producer.flush(timeout=10)
        _log.info(
            "exercise_injected exercise_id=%s topic=%s correlation_id=%s",
            exercise_id, topic, correlation_id,
        )


def _remap_scenario(original: Scenario, mapped_topics: list) -> Scenario:
    """
    Return a shallow copy of Scenario with steps remapped to sandbox topic names.
    The verifier operates on mapped names; this keeps verify() and trace aligned.
    """
    from shared.exercise.models import ScenarioStep
    new_steps = [
        ScenarioStep(
            topic=         mapped_topics[i] if i < len(mapped_topics) else step.topic,
            required_keys= step.required_keys,
            timeout_s=     step.timeout_s,
        )
        for i, step in enumerate(original.steps)
    ]
    return Scenario(
        name=         original.name,
        seed_topic=   original.seed_topic,
        seed_payload= original.seed_payload,
        steps=        new_steps,
        timeout_s=    original.timeout_s,
    )
