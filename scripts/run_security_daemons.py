from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from typing import Any

from babyai.security.runtime import SecurityRuntime


logger = logging.getLogger(__name__)


def _build_redis_client() -> Any | None:
    redis_url = str(os.getenv("REDIS_URL", "")).strip()
    if not redis_url:
        return None
    try:
        import redis  # type: ignore
    except Exception:
        logger.warning("redis package unavailable")
        return None
    try:
        client = redis.Redis.from_url(redis_url)
        client.ping()
        return client
    except Exception as exc:
        logger.warning("redis unavailable error=%s", exc)
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "daemon",
        choices=["consensus-engine", "trend-detector", "governance-agent"],
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

    redis_client = _build_redis_client()
    runtime = SecurityRuntime(
        redis_client=redis_client,
        sqlite_path=os.getenv("SQLITE_PATH", "/app/artifacts/security_events.sqlite"),
    )
    runtime.register_local_skills()

    daemon = str(args.daemon)
    logger.info("security_daemon_start daemon=%s", daemon)
    if daemon == "trend-detector":
        asyncio.run(runtime.trend_detector.run_loop())
        return 0
    if daemon == "governance-agent":
        asyncio.run(runtime.governance_agent.start())
        return 0
    while True:
        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
