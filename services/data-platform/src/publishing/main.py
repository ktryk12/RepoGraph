"""
services/publisher/main.py — PublishingService

Kafka consumer: content.publish.request
Kafka producer: content.published | content.publish.failed

Responsibility:
  - Receive a publish job from ContentOrchestratorAgent
  - Post content to the specified channel (twitter, youtube, linkedin, tiktok, newsletter)
  - Emit content.published on success with platform_ref
  - Emit content.publish.failed on error (no retry — human re-approves)

Supported channels and their env vars:
  twitter    : TWITTER_BEARER_TOKEN, TWITTER_API_KEY, TWITTER_API_SECRET,
               TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET
  youtube    : YOUTUBE_API_KEY, YOUTUBE_CHANNEL_ID (token refresh via TokenManager)
  linkedin   : LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET, LINKEDIN_REFRESH_TOKEN (refresh via TokenManager)
  tiktok     : TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET (refresh via TokenManager)
  newsletter : NEWSLETTER_API_KEY, NEWSLETTER_PROVIDER (mailchimp|beehiiv|stub)

stub mode (default when no API keys set): logs publish, returns dummy platform_ref.

L7: service executes only jobs dispatched after human approval.
requires_action is ALWAYS False in all emitted messages.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("publisher")

_BROKERS  = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BROKERS", "127.0.0.1:9092"))
_GROUP_ID = os.getenv("PUBLISHER_GROUP", "publisher-service")

_TOPIC_IN      = "content.publish.request"
_TOPIC_SUCCESS = "content.published"
_TOPIC_FAILED  = "content.publish.failed"

# Static keys (Twitter OAuth1.0a, Newsletter) — no refresh needed
_TWITTER_KEY         = os.getenv("TWITTER_API_KEY", "")
_NEWSLETTER_KEY      = os.getenv("NEWSLETTER_API_KEY", "")
_NEWSLETTER_PROVIDER = os.getenv("NEWSLETTER_PROVIDER", "stub")

# OAuth token manager (LinkedIn, TikTok, YouTube refresh)
from services.publisher.token_manager import TokenManager  # noqa: E402
_token_mgr = TokenManager()


# ---------------------------------------------------------------------------
# Channel adapters
# ---------------------------------------------------------------------------

def publish_to_channel(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Route to the correct channel adapter.
    Returns {"platform_ref": str, "channel": str} on success.
    Raises on failure.
    """
    channel = job.get("channel", "stub")

    if channel == "twitter":
        return _publish_twitter(job)
    if channel == "youtube":
        return _publish_youtube(job)
    if channel == "linkedin":
        return _publish_linkedin(job)
    if channel == "tiktok":
        return _publish_tiktok(job)
    if channel == "newsletter":
        return _publish_newsletter(job)

    # stub / unknown
    return _publish_stub(job)


def _publish_stub(job: Dict[str, Any]) -> Dict[str, Any]:
    ref = f"stub-{uuid.uuid4().hex[:8]}"
    _log.info(
        "publisher_stub channel=%s title=%s platform_ref=%s",
        job.get("channel", "stub"), job.get("title", "")[:60], ref,
    )
    return {"platform_ref": ref, "channel": job.get("channel", "stub")}


def _publish_twitter(job: Dict[str, Any]) -> Dict[str, Any]:
    if not _TWITTER_KEY:
        _log.info("publisher_twitter_stub_no_key")
        return _publish_stub(job)

    try:
        import tweepy  # type: ignore[import]
        client = tweepy.Client(
            consumer_key=_TWITTER_KEY,
            consumer_secret=os.getenv("TWITTER_API_SECRET", ""),
            access_token=os.getenv("TWITTER_ACCESS_TOKEN", ""),
            access_token_secret=os.getenv("TWITTER_ACCESS_SECRET", ""),
        )
        text = _compose_tweet(job)
        resp = client.create_tweet(text=text)
        tweet_id = resp.data["id"] if resp.data else "unknown"
        return {"platform_ref": f"twitter:{tweet_id}", "channel": "twitter"}
    except ImportError:
        _log.warning("publisher_twitter_tweepy_missing — stub mode")
        return _publish_stub(job)
    except Exception as exc:
        raise RuntimeError(f"twitter_publish_failed: {exc}") from exc


def _publish_youtube(job: Dict[str, Any]) -> Dict[str, Any]:
    token = _token_mgr.get_token("youtube")
    if not token:
        _log.info("publisher_youtube_stub_no_token")
        return _publish_stub(job)
    # YouTube upload requires video artifact — stub until artifact pipeline is live
    _log.info("publisher_youtube_requires_artifact — stub mode for now")
    return _publish_stub(job)


def _publish_linkedin(job: Dict[str, Any]) -> Dict[str, Any]:
    token = _token_mgr.get_token("linkedin")
    if not token:
        _log.info("publisher_linkedin_stub_no_token")
        return _publish_stub(job)

    try:
        import requests
        org_id = os.getenv("LINKEDIN_ORG_ID", "")
        text   = _compose_post(job)
        resp   = requests.post(
            "https://api.linkedin.com/v2/ugcPosts",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type":  "application/json",
            },
            json={
                "author":              f"urn:li:organization:{org_id}",
                "lifecycleState":      "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {"text": text},
                        "shareMediaCategory": "NONE",
                    }
                },
                "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
            },
            timeout=15,
        )
        resp.raise_for_status()
        post_id = resp.headers.get("x-restli-id", "unknown")
        return {"platform_ref": f"linkedin:{post_id}", "channel": "linkedin"}
    except Exception as exc:
        raise RuntimeError(f"linkedin_publish_failed: {exc}") from exc


def _publish_tiktok(job: Dict[str, Any]) -> Dict[str, Any]:
    token = _token_mgr.get_token("tiktok")
    if not token:
        _log.info("publisher_tiktok_stub_no_token")
        return _publish_stub(job)

    video_ref = job.get("video_ref", "")
    if not video_ref:
        _log.info("publisher_tiktok_stub_no_video_ref")
        return _publish_stub(job)

    try:
        import requests
        text = _compose_post(job)[:2200]
        resp = requests.post(
            "https://open.tiktokapis.com/v2/post/publish/video/init/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={
                "post_info": {
                    "title": text,
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                },
                "source_info": {
                    "source": "FILE_UPLOAD",
                    "video_size": job.get("video_size", 0),
                    "chunk_size": job.get("video_size", 0),
                    "total_chunk_count": 1,
                },
            },
            timeout=15,
        )
        resp.raise_for_status()
        publish_id = resp.json().get("data", {}).get("publish_id", "unknown")
        return {"platform_ref": f"tiktok:{publish_id}", "channel": "tiktok"}
    except Exception as exc:
        raise RuntimeError(f"tiktok_publish_failed: {exc}") from exc


def _publish_newsletter(job: Dict[str, Any]) -> Dict[str, Any]:
    if not _NEWSLETTER_KEY or _NEWSLETTER_PROVIDER == "stub":
        _log.info("publisher_newsletter_stub provider=%s", _NEWSLETTER_PROVIDER)
        return _publish_stub(job)
    # Provider-specific (Beehiiv / Mailchimp) — stub until configured
    _log.info("publisher_newsletter_provider_not_implemented provider=%s", _NEWSLETTER_PROVIDER)
    return _publish_stub(job)


# ---------------------------------------------------------------------------
# Content composers
# ---------------------------------------------------------------------------

def _compose_tweet(job: Dict[str, Any]) -> str:
    hook   = job.get("hook", "")
    symbol = job.get("symbol", "")
    points = job.get("key_points", [])[:2]
    lines  = [hook]
    if symbol:
        lines.append(f"${symbol}")
    lines.extend(f"• {p}" for p in points)
    text = "\n".join(lines)
    return text[:280]


def _compose_post(job: Dict[str, Any]) -> str:
    title  = job.get("title", "")
    hook   = job.get("hook", "")
    points = job.get("key_points", [])
    parts  = [title, "", hook, ""]
    parts.extend(f"• {p}" for p in points)
    return "\n".join(parts)[:3000]


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
    publish_id = job.get("publish_id", str(uuid.uuid4()))
    brief_id   = job.get("brief_id", "")
    channel    = job.get("channel", "stub")

    # Dry-run adapter hook — short-circuit before any external call
    from shared.exercise.hooks import is_dry_run
    if is_dry_run():
        _log.info("publisher_dry_run publish_id=%s channel=%s", publish_id, channel)
        result = {"platform_ref": f"dry-run-{publish_id[:8]}", "channel": channel}
        success = {
            "source":             "publisher_service",
            "publish_id":         publish_id,
            "brief_id":           brief_id,
            "published_at":       datetime.now(timezone.utc).isoformat(),
            "channel":            result["channel"],
            "platform_ref":       result["platform_ref"],
            "symbol":             job.get("symbol", ""),
            "video_ref":          job.get("video_ref"),
            "opportunity_score":  job.get("opportunity_score", 0.0),
            "requires_action":    False,
            # Pass through exercise metadata so tracer can correlate
            "exercise_id":        job.get("exercise_id", ""),
            "correlation_id":     job.get("correlation_id", ""),
            "dry_run":            True,
        }
        raw = json.dumps(success, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        producer.produce(topic=_TOPIC_SUCCESS, key=publish_id.encode(), value=raw)
        producer.flush(timeout=5)
        _log.info("publisher_dry_run_complete publish_id=%s platform_ref=%s",
                  publish_id, result["platform_ref"])
        return

    _log.info("publisher_job_start publish_id=%s channel=%s", publish_id, channel)

    try:
        result = publish_to_channel(job)

        success = {
            "source":             "publisher_service",
            "publish_id":         publish_id,
            "brief_id":           brief_id,
            "published_at":       datetime.now(timezone.utc).isoformat(),
            "channel":            result["channel"],
            "platform_ref":       result["platform_ref"],
            "symbol":             job.get("symbol", ""),
            "video_ref":          job.get("video_ref"),
            "opportunity_score":  job.get("opportunity_score", 0.0),
            "requires_action":    False,
        }
        raw = json.dumps(success, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        producer.produce(topic=_TOPIC_SUCCESS, key=publish_id.encode(), value=raw)
        producer.flush(timeout=5)
        _log.info(
            "publisher_job_complete publish_id=%s platform_ref=%s",
            publish_id, result["platform_ref"],
        )

    except Exception as exc:
        _log.error("publisher_job_failed publish_id=%s error=%s", publish_id, exc)
        failure = {
            "source":          "publisher_service",
            "publish_id":      publish_id,
            "brief_id":        brief_id,
            "failed_at":       datetime.now(timezone.utc).isoformat(),
            "channel":         channel,
            "error":           str(exc),
            "requires_action": False,
        }
        raw = json.dumps(failure, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        producer.produce(topic=_TOPIC_FAILED, key=publish_id.encode(), value=raw)
        producer.flush(timeout=5)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _log.info("publisher_service starting")
    consumer = _build_consumer()
    producer = _build_producer()
    consumer.subscribe([_TOPIC_IN])
    _log.info("publisher_service subscribed topic=%s", _TOPIC_IN)

    try:
        while True:
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                _log.warning("publisher_kafka_error error=%s", msg.error())
                continue
            try:
                job = json.loads(msg.value().decode("utf-8"))
                _process_job(job, producer)
            except Exception as exc:
                _log.error("publisher_job_error error=%s", exc, exc_info=True)
    except KeyboardInterrupt:
        _log.info("publisher_service shutting down")
    finally:
        consumer.close()
        producer.flush(timeout=5)


if __name__ == "__main__":
    main()
