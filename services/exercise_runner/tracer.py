"""
services/exercise_runner/tracer.py — Race-free Kafka event observer.

Fix for race condition:
  The consumer MUST subscribe and reach assignment BEFORE the seed event is
  injected. Tracer.arm() blocks until partitions are assigned, then signals
  the runner to inject. collect() is called after injection.

Lifecycle (enforced by ExerciseRunner):
  1. tracer = Tracer(watch_topics, brokers)
  2. tracer.arm()           ← subscribes, blocks until partitions assigned
  3. runner._inject(...)    ← safe to inject now — consumer is listening
  4. trace = tracer.collect(exercise_id, timeout_s)
  5. tracer.close()
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import List, Optional, Set

_log = logging.getLogger("exercise-tracer")

# How long to wait for partition assignment before giving up (seconds)
_ARM_TIMEOUT_S = 15.0
# Poll interval during arm phase
_ARM_POLL_MS   = 0.2


class Tracer:
    """
    Subscribes to watch_topics and collects TraceEvents filtered by exercise_id.

    Not thread-safe — use from a single thread (the runner).
    """

    def __init__(self, watch_topics: List[str], brokers: str) -> None:
        self._topics   = watch_topics
        self._brokers  = brokers
        self._consumer = None
        self._armed    = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def arm(self, exercise_id: str) -> None:
        """
        Subscribe and block until Kafka assigns partitions.
        MUST be called before the seed event is injected.

        Raises RuntimeError if partitions are not assigned within _ARM_TIMEOUT_S.
        """
        try:
            from confluent_kafka import Consumer
        except ImportError as exc:
            raise RuntimeError("confluent_kafka not available") from exc

        self._consumer = Consumer({
            "bootstrap.servers":  self._brokers,
            "group.id":           f"exercise-tracer-{exercise_id}",
            "auto.offset.reset":  "latest",
            "enable.auto.commit": False,
        })
        self._consumer.subscribe(self._topics)

        # Block until assignment is non-empty
        deadline = time.monotonic() + _ARM_TIMEOUT_S
        while time.monotonic() < deadline:
            self._consumer.poll(_ARM_POLL_MS)
            assignment = self._consumer.assignment()
            if assignment:
                _log.info(
                    "tracer_armed exercise_id=%s topics=%s partitions=%d",
                    exercise_id, self._topics, len(assignment),
                )
                self._armed = True
                return
            time.sleep(0.05)

        self._consumer.close()
        raise RuntimeError(
            f"tracer_arm_timeout: no partition assignment after {_ARM_TIMEOUT_S}s "
            f"for topics={self._topics}"
        )

    def collect(
        self,
        exercise_id: str,
        timeout_s:   float,
        expected_topics: Optional[List[str]] = None,
    ):
        """
        Poll for events until timeout or all expected_topics seen.

        Returns Trace. Never raises — on error, returns partial trace.
        Must be called after arm().
        """
        from shared.exercise.models import Trace, TraceEvent

        if not self._armed or self._consumer is None:
            raise RuntimeError("tracer.arm() must be called before collect()")

        events   = []
        seen:    Set[str] = set()
        t0       = time.monotonic()
        deadline = t0 + timeout_s
        watch    = set(expected_topics or self._topics)

        try:
            while time.monotonic() < deadline:
                msg = self._consumer.poll(0.5)
                if msg is None:
                    continue
                if msg.error():
                    _log.warning("tracer_kafka_error error=%s", msg.error())
                    continue

                try:
                    payload = json.loads(msg.value().decode("utf-8"))
                except Exception:
                    payload = {}

                # Filter: only events from this exercise run
                if payload.get("exercise_id") != exercise_id:
                    continue

                event = TraceEvent(
                    topic=          msg.topic(),
                    key=            (msg.key() or b"").decode(),
                    received_at=    datetime.now(timezone.utc),
                    latency_ms=     (time.monotonic() - t0) * 1000,
                    payload_keys=   list(payload.keys()),
                    exercise_id=    exercise_id,
                    correlation_id= payload.get("correlation_id", ""),
                )
                events.append(event)
                seen.add(msg.topic())

                _log.debug(
                    "tracer_event topic=%s latency_ms=%.0f exercise_id=%s",
                    msg.topic(), event.latency_ms, exercise_id,
                )

                # Early exit when all expected topics received
                if watch and watch.issubset(seen):
                    _log.info(
                        "tracer_all_expected_seen exercise_id=%s topics=%s",
                        exercise_id, list(watch),
                    )
                    break

        except Exception as exc:
            _log.error("tracer_collect_error exercise_id=%s error=%s", exercise_id, exc)

        from shared.exercise.models import Trace
        return Trace(
            exercise_id=exercise_id,
            scenario="",
            started_at=datetime.now(timezone.utc),
            events=events,
        )

    def close(self) -> None:
        if self._consumer:
            try:
                self._consumer.close()
            except Exception:
                pass
            self._consumer = None
            self._armed    = False
