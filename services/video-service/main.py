"""
services/claude-video/main.py — ClaudeVideoService

Kafka consumer: content.video.request
Kafka producer: content.video.complete

Responsibility:
  - Receive a render job from ContentOrchestratorAgent
  - Generate video script via Claude
  - Submit to configured video renderer (e.g. RunwayML, Synthesia, HeyGen)
  - Store artifact and emit content.video.complete with artifact_ref

Env vars:
  KAFKA_BOOTSTRAP_SERVERS  : default 127.0.0.1:9092
  VIDEO_RENDERER           : "stub" | "runway" | "synthesia" | "heygen"
  VIDEO_RENDERER_API_KEY   : API key for chosen renderer
  BABYAI_ARTIFACT_STORE    : path prefix for local artifact storage (default: artifacts/)
  CLAUDE_MODEL             : model for script generation (default: claude-sonnet-4-6)

MODE=stub (default when VIDEO_RENDERER_API_KEY not set):
  Writes a .txt script file as the artifact; no external API call.

L7: service never initiates actions — it only executes jobs dispatched by the orchestrator
after human approval.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("claude-video")

_BROKERS       = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BROKERS", "127.0.0.1:9092"))
_GROUP_ID      = os.getenv("CLAUDE_VIDEO_GROUP", "claude-video-service")
_RENDERER      = os.getenv("VIDEO_RENDERER", "stub")
_RENDERER_KEY  = os.getenv("VIDEO_RENDERER_API_KEY", "")
_ARTIFACT_DIR  = Path(os.getenv("BABYAI_ARTIFACT_STORE", "artifacts"))
_CLAUDE_MODEL  = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

_TOPIC_IN   = "content.video.request"
_TOPIC_OUT  = "content.video.complete"

_TEMPLATES = {"fact_check_short", "default"}


# ---------------------------------------------------------------------------
# Script generator
# ---------------------------------------------------------------------------

def generate_script(job: Dict[str, Any]) -> str:
    """Generate video script via Claude. Routes to template-specific prompt."""
    template = str(job.get("template", "default"))
    if template == "fact_check_short":
        return _generate_fact_check_script(job)
    return _generate_default_script(job)


def _generate_default_script(job: Dict[str, Any]) -> str:
    title       = job.get("title", "")
    hook        = job.get("hook", "")
    key_points  = job.get("key_points", [])
    tone        = job.get("tone", "analytical")
    length_s    = int(job.get("target_length_s", 90))
    try:
        import anthropic
        client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY", "local"),
            base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        )
        prompt = _script_prompt(title, hook, key_points, tone, length_s)
        resp   = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text if resp.content else _template_script(title, hook, key_points)
    except Exception as exc:
        _log.warning("claude_video_script_fallback error=%s", exc)
        return _template_script(title, hook, key_points)


def _generate_fact_check_script(job: Dict[str, Any]) -> str:
    """
    Fact-check short format: 30-90 seconds.
    Required job fields: claim_text, verdict, confidence, context_note, sources.
    Produces: hook → claim → verdict stamp → evidence → CTA.
    """
    claim_text   = str(job.get("claim_text", job.get("hook", "")))
    verdict      = str(job.get("verdict", "UNVERIFIED")).upper()
    confidence   = float(job.get("confidence", 0.0))
    context_note = str(job.get("context_note", ""))
    sources      = job.get("sources", [])[:3]
    length_s     = int(job.get("target_length_s", 60))

    source_lines = "\n".join(
        f"  - {s.get('title', s.get('url', ''))[:80]}" for s in sources
    )
    verdict_emoji = {"TRUE": "✅", "FALSE": "❌", "MISLEADING": "⚠️",
                     "UNVERIFIED": "❓", "SATIRE": "😄"}.get(verdict, "❓")

    try:
        import anthropic
        client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY", "local"),
            base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
        )
        prompt = f"""Write a {length_s}-second fact-check video script. Tone: clear, direct, neutral.

Claim being checked: {claim_text}

Verdict: {verdict} {verdict_emoji} (confidence: {confidence:.0%})
Context: {context_note}
Sources used:
{source_lines}

Script format — 5 beats:
[HOOK 0-5s] Attention-grabbing one-liner about the claim
[CLAIM 5-15s] State the claim exactly as it circulated
[VERDICT 15-30s] {verdict} stamp + one-sentence explanation
[EVIDENCE 30-{length_s-10}s] Reference 1-2 key sources supporting the verdict
[CTA {length_s-10}-{length_s}s] "Follow for daily fact-checks" + source link prompt

Rules: no scene directions, plain text only, spoken at ~140 wpm."""
        resp = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text if resp.content else _fact_check_template_script(
            claim_text, verdict, verdict_emoji, context_note, sources
        )
    except Exception as exc:
        _log.warning("claude_video_fact_check_script_fallback error=%s", exc)
        return _fact_check_template_script(claim_text, verdict, verdict_emoji, context_note, sources)


def _fact_check_template_script(
    claim_text: str, verdict: str, emoji: str, context_note: str, sources: list
) -> str:
    source_line = sources[0].get("title", sources[0].get("url", "")) if sources else "primærkilder"
    return f"""[HOOK]
Er det sandt? Vi tjekker det.

[CLAIM]
Påstanden: {claim_text[:200]}

[VERDICT]
{emoji} Vores dom: {verdict}
{context_note}

[EVIDENCE]
Baseret på: {source_line[:100]}

[CTA]
Følg for daglige faktatjeks. Link i bio.
"""


def _script_prompt(title: str, hook: str, key_points: list, tone: str, length_s: int) -> str:
    points_str = "\n".join(f"  - {p}" for p in key_points)
    return f"""Write a {length_s}-second video script in a {tone} tone.

Title: {title}
Opening hook: {hook}
Key points to cover:
{points_str}

Format: plain text, scene directions in [brackets], no timestamps.
Keep it tight — {length_s} seconds spoken at ~150 wpm ≈ {length_s * 150 // 60} words."""


def _template_script(title: str, hook: str, key_points: list) -> str:
    points_str = "\n".join(f"[POINT] {p}" for p in key_points)
    return f"""[INTRO]
{hook}

[TITLE CARD: {title}]

{points_str}

[OUTRO]
Follow for more signals. Like if this was useful.
"""


# ---------------------------------------------------------------------------
# Renderer adapter
# ---------------------------------------------------------------------------

def render_video(script: str, job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Submit script to configured renderer. Returns artifact metadata.
    stub mode: writes script to file, returns local path as artifact_ref.
    """
    artifact_id = str(uuid.uuid4())

    if _RENDERER == "stub" or not _RENDERER_KEY:
        return _render_stub(script, artifact_id, job)

    if _RENDERER == "runway":
        return _render_runway(script, artifact_id, job)

    _log.warning("claude_video_unknown_renderer renderer=%s — falling back to stub", _RENDERER)
    return _render_stub(script, artifact_id, job)


def _render_stub(script: str, artifact_id: str, job: Dict[str, Any]) -> Dict[str, Any]:
    """Write script as .txt artifact — no external API."""
    _ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    template = str(job.get("template", "default"))
    path = _ARTIFACT_DIR / f"video_script_{artifact_id}.txt"
    path.write_text(script, encoding="utf-8")
    _log.info("claude_video_stub_rendered artifact_id=%s template=%s path=%s",
              artifact_id, template, path)
    result: Dict[str, Any] = {
        "artifact_id":  artifact_id,
        "artifact_ref": str(path),
        "renderer":     "stub",
        "format":       "script_txt",
        "duration_s":   job.get("target_length_s", 0),
        "template":     template,
    }
    if template == "fact_check_short":
        result["verdict"]       = job.get("verdict", "UNVERIFIED")
        result["confidence"]    = job.get("confidence", 0.0)
        result["claim_id"]      = job.get("claim_id", "")
        result["requires_review"] = True
    return result


def _render_runway(script: str, artifact_id: str, job: Dict[str, Any]) -> Dict[str, Any]:
    """Placeholder for RunwayML integration."""
    _log.info("claude_video_runway_not_implemented — falling back to stub")
    return _render_stub(script, artifact_id, job)


# ---------------------------------------------------------------------------
# Kafka I/O
# ---------------------------------------------------------------------------

def _build_consumer():
    from confluent_kafka import Consumer
    return Consumer({
        "bootstrap.servers":  _BROKERS,
        "group.id":           _GROUP_ID,
        "auto.offset.reset":  "latest",
        "enable.auto.commit": True,
    })


def _build_producer():
    from confluent_kafka import Producer
    return Producer({"bootstrap.servers": _BROKERS, "acks": "all"})


def _process_job(job: Dict[str, Any], producer) -> None:
    brief_id = job.get("brief_id", "")
    job_id   = job.get("job_id", str(uuid.uuid4()))

    # Dry-run adapter hook — skip real render, emit synthetic complete event
    from shared.exercise.hooks import is_dry_run
    if is_dry_run():
        _log.info("claude_video_dry_run job_id=%s brief_id=%s", job_id, brief_id)
        result = {
            "source":        "claude_video_service",
            "job_id":        job_id,
            "brief_id":      brief_id,
            "completed_at":  datetime.now(timezone.utc).isoformat(),
            "artifact_ref":  f"dry-run-script-{job_id[:8]}.txt",
            "artifact_id":   job_id,
            "renderer":      "dry_run",
            "duration_s":    job.get("target_length_s", 0),
            # Pass through exercise metadata so tracer can correlate
            "exercise_id":   job.get("exercise_id", ""),
            "correlation_id": job.get("correlation_id", ""),
            "dry_run":       True,
        }
        raw = json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        producer.produce(topic=_TOPIC_OUT, key=job_id.encode(), value=raw)
        producer.flush(timeout=5)
        _log.info("claude_video_dry_run_complete job_id=%s", job_id)
        return

    _log.info("claude_video_job_start job_id=%s brief_id=%s", job_id, brief_id)

    script   = generate_script(job)
    artifact = render_video(script, job)

    result: Dict[str, Any] = {
        "source":       "claude_video_service",
        "job_id":       job_id,
        "brief_id":     brief_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "artifact_ref": artifact["artifact_ref"],
        "artifact_id":  artifact["artifact_id"],
        "renderer":     artifact["renderer"],
        "duration_s":   artifact.get("duration_s", 0),
        "template":     artifact.get("template", "default"),
    }
    if artifact.get("template") == "fact_check_short":
        result["verdict"]         = artifact.get("verdict", "UNVERIFIED")
        result["confidence"]      = artifact.get("confidence", 0.0)
        result["claim_id"]        = artifact.get("claim_id", "")
        result["requires_review"] = True

    raw = json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    producer.produce(topic=_TOPIC_OUT, key=job_id.encode(), value=raw)
    producer.flush(timeout=5)
    _log.info("claude_video_job_complete job_id=%s artifact_ref=%s", job_id, artifact["artifact_ref"])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _log.info("claude_video_service starting renderer=%s", _RENDERER)
    consumer = _build_consumer()
    producer = _build_producer()
    consumer.subscribe([_TOPIC_IN])
    _log.info("claude_video_service subscribed topic=%s", _TOPIC_IN)

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                _log.warning("claude_video_kafka_error error=%s", msg.error())
                continue
            try:
                job = json.loads(msg.value().decode("utf-8"))
                _process_job(job, producer)
            except Exception as exc:
                _log.error("claude_video_job_error error=%s", exc, exc_info=True)
    except KeyboardInterrupt:
        _log.info("claude_video_service shutting down")
    finally:
        consumer.close()
        producer.flush(timeout=5)


if __name__ == "__main__":
    main()
