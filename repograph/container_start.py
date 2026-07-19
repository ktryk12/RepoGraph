"""Container bootstrap: migrate Postgres, then start the RepoGraph API."""

from __future__ import annotations

import logging
import os
import time

import uvicorn

LOGGER = logging.getLogger(__name__)
_FALSE_VALUES = {"0", "false", "no", "off"}


def run_migrations_with_retry() -> None:
    """Apply pending migrations after Postgres becomes reachable."""
    dsn = os.getenv("REPOGRAPH_POSTGRES_DSN", "")
    enabled = os.getenv("REPOGRAPH_AUTO_MIGRATE", "1").strip().lower() not in _FALSE_VALUES
    if not dsn or not enabled:
        LOGGER.info("Postgres auto-migration skipped")
        return

    attempts = max(1, int(os.getenv("REPOGRAPH_MIGRATE_ATTEMPTS", "30")))
    interval = max(0.1, float(os.getenv("REPOGRAPH_MIGRATE_INTERVAL", "2")))
    from repograph.postgres.migrate import run

    for attempt in range(1, attempts + 1):
        try:
            run(dsn)
            LOGGER.info("Postgres migrations are current")
            return
        except Exception as exc:
            if attempt == attempts:
                raise RuntimeError(
                    f"Postgres migration failed after {attempts} attempts"
                ) from exc
            LOGGER.warning(
                "Postgres is not ready for migration (%s/%s): %s",
                attempt,
                attempts,
                exc,
            )
            time.sleep(interval)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    run_migrations_with_retry()

    host = os.getenv("REPOGRAPH_HOST", "0.0.0.0")
    port = int(os.getenv("REPOGRAPH_PORT", "8001"))
    reload_enabled = os.getenv("REPOGRAPH_DEV_RELOAD", "").strip().lower() not in _FALSE_VALUES | {""}
    if reload_enabled:
        uvicorn.run(
            "repograph.api.routes:app",
            host=host,
            port=port,
            reload=True,
            reload_dirs=["/app/repograph"],
        )
        return
    uvicorn.run("repograph.api.routes:app", host=host, port=port)


if __name__ == "__main__":
    main()
