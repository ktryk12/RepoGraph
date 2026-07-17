"""
GapDetectorAgent — observes Kafka topic coverage and reports gaps for human review.

Runs a 300-second (5 min) analysis cycle.  Each cycle:
  1. Checks DLQ topics for accumulation (topics ending in .dlq)
  2. Scans all known topics via KafkaProvisioner for consumer lag / no consumers
  3. Correlates: topic has messages but zero active consumers → confirmed gap
  4. Scores confidence and filters noise (< 0.50 → discarded)
  5. Writes GapReport to logs/gap_detector.log (JSON lines) and
     logs/gap_proposals.md (human-readable Markdown)
  6. Publishes signal to signal.infra.gap for confidence >= 0.70

L7 boundary — requires_action is ALWAYS False.
No automatic agent creation.  Human must run:
    python -m babyai.cli approve-gap <gap_id>
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.kafka_provisioner import KafkaProvisioner

_log = logging.getLogger(__name__)

# ── Kafka topic constants (inline — avoids bus/__init__ → babyai_shared chain) ──
SIGNAL_INFRA_GAP = "signal.infra.gap"

# All known topics to scan for gaps (mirrors bus/topics.py key entries)
_KNOWN_TOPICS: List[str] = [
    # Tier 1
    "decision.intent", "decision.truthpack.questions", "decision.truthpack.answers",
    "decision.truthpack.ready", "decision.requested", "decision.lifecycle",
    "decision.approval", "policy.discovery.complete", "policy.draft.ready",
    "policy.approved", "policy.rejected", "eval.results", "tool.events", "artifact.events",
    # Tier 2 — runtime
    "agent.observations.raw", "agent.observations.normalized", "agent.latent_packets",
    "swarm.events", "swarm.directives",
    # Tier 2 — crypto signals
    "signal.crypto.whale", "signal.crypto.market", "signal.crypto.newproject",
    # Tier 2 — infra signals
    "signal.infra.gap", "signal.infra.bootstrap.complete", "signal.infra.bootstrap.failed",
    # Agent registry
    "agent.discovered", "agent.ready", "agent.heartbeat", "agent.stopped",
]

_DLQ_TOPICS: List[str] = [
    "policy.bootstrap.dlq",
    "decision.lifecycle.dlq",
]

_POLL_INTERVAL   = 300       # seconds
_LAG_THRESHOLD   = 100       # messages; above this → high_lag
_MIN_CONFIDENCE  = 0.50      # below this → noise-filtered, not reported

_BROKERS   = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BROKERS", "127.0.0.1:9092"))
_LOG_DIR   = Path(os.getenv("BABYAI_LOG_DIR", "logs"))


# ---------------------------------------------------------------------------
# Data Transfer Objects
# ---------------------------------------------------------------------------

@dataclass
class GapReport:
    """A single detected coverage gap awaiting human review."""

    gap_id:                str
    detected_at:           str
    topic:                 str
    message_count:         int
    oldest_message_age_s:  float
    gap_type:              str            # "no_consumer" | "high_lag" | "dlq_accumulation"
    suggested_agent:       str
    suggested_topics:      List[str]
    confidence:            float
    examples:              List[Dict[str, Any]] = field(default_factory=list)
    requires_human_approval: bool = True  # ALWAYS True — L7 boundary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gap_id":                 self.gap_id,
            "detected_at":            self.detected_at,
            "topic":                  self.topic,
            "message_count":          self.message_count,
            "oldest_message_age_s":   self.oldest_message_age_s,
            "gap_type":               self.gap_type,
            "suggested_agent":        self.suggested_agent,
            "suggested_topics":       self.suggested_topics,
            "confidence":             self.confidence,
            "examples":               self.examples,
            "requires_human_approval": self.requires_human_approval,
        }


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _suggest_agent(topic: str) -> str:
    """Derive a human-readable agent suggestion from a topic name."""
    if topic.startswith("signal.crypto."):
        return "CryptoSignalAgent"
    if topic.startswith("signal.macro."):
        return "MacroPolicyAgent"
    if topic.startswith("signal.political."):
        return "PoliticalIntelAgent"
    if topic.startswith("signal.institutional."):
        return "WhaleWatcherAgent"
    if topic.startswith("signal.analysis."):
        return "DeepAnalysisAgent"
    if topic.startswith("decision."):
        return "DecisionHandlerAgent"
    return "UnknownAgent (manual review required)"


def _score_confidence(message_count: int, age_s: float) -> float:
    """
    Confidence scoring based on message volume and age.

    Returns 0.0 for noise (below minimum threshold).
    """
    if message_count > 100 and age_s > 3600:
        return 0.90
    if message_count > 10 and age_s > 600:
        return 0.70
    if message_count > 0 and age_s > 60:
        return 0.50
    return 0.0


# ---------------------------------------------------------------------------
# Log writers
# ---------------------------------------------------------------------------

class _LogWriter:
    """Writes GapReports to JSON lines log and human-readable Markdown."""

    def __init__(self, log_dir: Path = _LOG_DIR) -> None:
        self._log_dir   = log_dir
        self._json_log  = log_dir / "gap_detector.log"
        self._md_log    = log_dir / "gap_proposals.md"

    def write(self, report: GapReport) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._write_json(report)
        self._write_markdown(report)

    def _write_json(self, report: GapReport) -> None:
        entry = {
            "event":                  "gap_detected",
            "status":                 "pending",
            **report.to_dict(),
        }
        try:
            with self._json_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=True, separators=(",", ":")) + "\n")
        except Exception as exc:
            _log.error("gap_detector_json_log_failed path=%s error=%s", self._json_log, exc)

    def _write_markdown(self, report: GapReport) -> None:
        age_h = report.oldest_message_age_s / 3600
        topics_str = ", ".join(report.suggested_topics) if report.suggested_topics else report.topic
        block = (
            "\n---\n"
            f"## Gap Detected: {report.detected_at}\n"
            f"**Topic:** {report.topic}\n"
            f"**Type:** {report.gap_type}\n"
            f"**Messages waiting:** {report.message_count} (oldest: {age_h:.1f} hours ago)\n"
            f"**Confidence:** {report.confidence:.2f}\n"
            f"**Suggested agent:** {report.suggested_agent}\n"
            f"**Suggested topics:** {topics_str}\n"
            "**Status:** PENDING HUMAN APPROVAL\n"
            f"**Gap ID:** {report.gap_id} (use this to approve via CLI)\n\n"
            "To approve, run:\n"
            f"  python -m babyai.cli approve-gap {report.gap_id}\n\n"
            "To reject, run:\n"
            f"  python -m babyai.cli reject-gap {report.gap_id}\n\n"
            "---\n"
        )
        try:
            with self._md_log.open("a", encoding="utf-8") as fh:
                fh.write(block)
        except Exception as exc:
            _log.error("gap_detector_md_log_failed path=%s error=%s", self._md_log, exc)


# ---------------------------------------------------------------------------
# Kafka publisher (same pattern as CryptoIntelAgent)
# ---------------------------------------------------------------------------

class _GapPublisher:
    def __init__(self, brokers: str = _BROKERS) -> None:
        self._producer: Optional[Any] = None
        try:
            from confluent_kafka import Producer
            self._producer = Producer({"bootstrap.servers": brokers, "acks": "all"})
            _log.info("gap_detector_kafka_ready brokers=%s", brokers)
        except Exception as exc:
            _log.warning("gap_detector_kafka_unavailable error=%s", exc)

    def publish(self, report: GapReport) -> None:
        if self._producer is None:
            _log.info("gap_detector_publish_skip_no_kafka gap_id=%s", report.gap_id)
            return
        payload = {
            "source":                  "gap_detector_agent",
            "timestamp":               datetime.now(timezone.utc).isoformat(),
            "signal_type":             "infra_gap",
            "gap_report":              report.to_dict(),
            "requires_action":         False,   # L7: ALWAYS False
            "requires_human_approval": True,
        }
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        try:
            self._producer.produce(
                topic=SIGNAL_INFRA_GAP,
                key=report.gap_id.encode("utf-8"),
                value=raw,
            )
            self._producer.poll(0)
        except Exception as exc:
            _log.error("gap_detector_publish_failed gap_id=%s error=%s", report.gap_id, exc)

    def flush(self) -> None:
        if self._producer:
            try:
                self._producer.flush(timeout=5)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# GapDetectorAgent
# ---------------------------------------------------------------------------

class GapDetectorAgent:
    """
    Autonomous gap detection agent.

    Scans Kafka topic coverage every 300 seconds, builds GapReports
    for uncovered topics, and logs proposals for human review.

    L7 boundary: requires_action is ALWAYS False.  No automatic
    agent creation.  Human must approve via CLI.

    Example::

        agent = GapDetectorAgent()
        agent.start()   # non-blocking background thread
        ...
        agent.stop()
    """

    agent_id: str = "gap-detector-001"
    role:     str = "infrastructure-observer"

    def __init__(
        self,
        provisioner:   KafkaProvisioner | None = None,
        publisher:     _GapPublisher    | None = None,
        log_writer:    _LogWriter       | None = None,
        poll_interval: float = _POLL_INTERVAL,
        log_dir:       Path  = _LOG_DIR,
    ) -> None:
        self._prov     = provisioner or KafkaProvisioner(brokers=_BROKERS)
        self._pub      = publisher   or _GapPublisher()
        self._writer   = log_writer  or _LogWriter(log_dir=log_dir)
        self._interval = poll_interval

        # Observation window: {topic: first_seen_with_lag_ts}
        self._first_seen: Dict[str, float] = {}

        self._thread:   Optional[threading.Thread] = None
        self._stop_evt  = threading.Event()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start background polling thread (non-blocking)."""
        if self._thread and self._thread.is_alive():
            _log.warning("gap_detector_agent already running")
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="gap-detector-agent",
            daemon=True,
        )
        self._thread.start()
        _log.info("gap_detector_agent started interval=%ss", self._interval)

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=15)
        _log.info("gap_detector_agent stopped")

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── Main loop ─────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self._analysis_cycle()
            except Exception as exc:
                _log.error("gap_detector_cycle_error error=%s", exc, exc_info=True)
            self._stop_evt.wait(timeout=self._interval)

    def _analysis_cycle(self) -> None:
        _log.debug("gap_detector_cycle start")
        all_groups  = self._prov.list_consumer_groups()
        gap_reports = []

        # 1. Check DLQ topics
        for topic in _DLQ_TOPICS:
            report = self._check_topic(
                topic, all_groups, gap_type_override="dlq_accumulation"
            )
            if report:
                gap_reports.append(report)

        # 2. Scan all known topics
        for topic in _KNOWN_TOPICS:
            if any(r.topic == topic for r in gap_reports):
                continue  # already captured as DLQ
            report = self._check_topic(topic, all_groups)
            if report:
                gap_reports.append(report)

        # 3. Emit reports
        for report in gap_reports:
            self._emit(report)

        self._pub.flush()
        _log.debug("gap_detector_cycle done reports=%d", len(gap_reports))

    def _check_topic(
        self,
        topic:              str,
        all_groups:         List[str],
        gap_type_override:  Optional[str] = None,
    ) -> Optional[GapReport]:
        """Analyse a single topic for coverage gaps. Returns GapReport or None."""
        now = time.time()

        if not all_groups:
            # Cannot determine consumers; check watermark directly
            lag_total = self._watermark_count(topic)
            if lag_total == 0:
                return None
            gap_type = gap_type_override or "no_consumer"
        else:
            # Find max lag across all groups for this topic
            max_lag   = 0
            has_group = False
            for group_id in all_groups:
                lags = self._prov.get_topic_lag(group_id, topic)
                if lags:
                    has_group = True
                    max_lag   = max(max_lag, sum(lags.values()))

            if not has_group:
                # Nobody is consuming this topic at all
                lag_total = self._watermark_count(topic)
                if lag_total == 0:
                    return None
                gap_type  = gap_type_override or "no_consumer"
            elif max_lag > _LAG_THRESHOLD:
                lag_total = max_lag
                gap_type  = gap_type_override or "high_lag"
            else:
                # Topic is covered and not lagging — clean
                self._first_seen.pop(topic, None)
                return None

        # Track first observation of this gap
        first_ts = self._first_seen.setdefault(topic, now)
        age_s    = now - first_ts

        confidence = _score_confidence(lag_total, age_s)
        if confidence < _MIN_CONFIDENCE:
            return None   # noise filter

        return GapReport(
            gap_id                = str(uuid.uuid4()),
            detected_at           = datetime.now(timezone.utc).isoformat(),
            topic                 = topic,
            message_count         = lag_total,
            oldest_message_age_s  = age_s,
            gap_type              = gap_type,
            suggested_agent       = _suggest_agent(topic),
            suggested_topics      = [topic],
            confidence            = confidence,
            requires_human_approval = True,
        )

    def _watermark_count(self, topic: str) -> int:
        """Return total messages in a topic (no consumer group) using watermarks."""
        try:
            from confluent_kafka import Consumer, TopicPartition
            consumer = Consumer({
                "bootstrap.servers":  _BROKERS,
                "group.id":           f"gap-detector-probe-{uuid.uuid4().hex[:8]}",
                "enable.auto.commit": False,
            })
            try:
                meta = consumer.list_topics(topic=topic, timeout=5.0)
                if topic not in meta.topics:
                    return 0
                total = 0
                for partition_id in meta.topics[topic].partitions:
                    tp       = TopicPartition(topic, partition_id)
                    lo, hi   = consumer.get_watermark_offsets(tp, timeout=5.0)
                    total   += max(0, hi - lo)
                return total
            finally:
                consumer.close()
        except Exception as exc:
            _log.debug("gap_detector_watermark_failed topic=%s error=%s", topic, exc)
            return 0

    def _emit(self, report: GapReport) -> None:
        if report.confidence < _MIN_CONFIDENCE:
            _log.debug(
                "gap_detector_noise_filtered gap_id=%s confidence=%.2f",
                report.gap_id, report.confidence,
            )
            return
        _log.info(
            "gap_detected gap_id=%s topic=%s gap_type=%s confidence=%.2f message_count=%d",
            report.gap_id, report.topic, report.gap_type,
            report.confidence, report.message_count,
        )
        self._writer.write(report)
        if report.confidence >= 0.70:
            self._pub.publish(report)
