"""
services/exercise_runner/verifier.py — Scenario result verifier.

Checks:
  1. Topic order   — all expected_topics present in trace
  2. Contracts     — topic-specific required keys present on each event
  3. Correlation   — every exercise event carries exercise_id + correlation_id
                     (implicit contract, always checked)

Verdicts:
  PASS    — all steps seen, all contracts satisfied
  PARTIAL — some steps seen, chain broke mid-way
  FAIL    — first expected topic never received (stuck at start)
"""
from __future__ import annotations

import logging
from typing import Dict, List

from shared.exercise.models import (
    ContractFailure,
    Scenario,
    ScenarioResult,
    Trace,
    TraceEvent,
)

_log = logging.getLogger("exercise-verifier")

# Implicit keys every exercise event must carry (correlation check)
_IMPLICIT_KEYS = {"exercise_id", "correlation_id"}


class Verifier:
    def verify(self, scenario: Scenario, trace: Trace) -> ScenarioResult:
        seen_topics   = trace.topics_seen()
        missing       = [t for t in scenario.expected_topics if t not in seen_topics]
        failures      = self._check_contracts(scenario, trace)

        # Verdict logic
        if not missing and not failures:
            verdict = "PASS"
        elif missing and missing[0] == scenario.expected_topics[0]:
            # First expected topic never arrived — stuck before start
            verdict = "FAIL"
        else:
            verdict = "PARTIAL"

        first_missing = missing[0] if missing else None
        last_seen     = trace.last_seen_topic()

        if missing or failures:
            _log.warning(
                "verifier_%s scenario=%s missing=%s contract_failures=%d",
                verdict.lower(), scenario.name, first_missing, len(failures),
            )
        else:
            _log.info("verifier_pass scenario=%s events=%d", scenario.name, trace.event_count())

        return ScenarioResult(
            scenario=          scenario.name,
            exercise_id=       trace.exercise_id,
            verdict=           verdict,
            mode=              "",          # filled in by runner
            trace=             trace,
            missing_topics=    missing,
            contract_failures= failures,
            first_missing=     first_missing,
            last_seen=         last_seen,
            event_count=       trace.event_count(),
            error=             self._summarise_failures(failures),
            duration_ms=       0.0,        # filled in by runner
        )

    # ── Contract checks ───────────────────────────────────────────────────────

    def _check_contracts(
        self, scenario: Scenario, trace: Trace
    ) -> List[ContractFailure]:
        failures: List[ContractFailure] = []

        for event in trace.events:
            # 1. Implicit correlation check (all exercise events)
            for key in _IMPLICIT_KEYS:
                if key not in event.payload_keys:
                    failures.append(ContractFailure(topic=event.topic, missing_key=key))
                    _log.warning(
                        "verifier_contract_implicit topic=%s missing=%s exercise_id=%s",
                        event.topic, key, event.exercise_id,
                    )

            # 2. Topic-specific contract from Scenario.steps
            for required_key in scenario.required_keys_for(event.topic):
                if required_key not in event.payload_keys:
                    failures.append(ContractFailure(topic=event.topic, missing_key=required_key))
                    _log.warning(
                        "verifier_contract_step topic=%s missing=%s",
                        event.topic, required_key,
                    )

        return failures

    def _summarise_failures(self, failures: List[ContractFailure]) -> str | None:
        if not failures:
            return None
        return "; ".join(f"{f.topic} missing key '{f.missing_key}'" for f in failures[:5])
