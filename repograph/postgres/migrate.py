"""Kør alle Postgres-migrationer i rækkefølge."""
from __future__ import annotations

import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run(dsn: str | None = None) -> None:
    dsn = dsn or os.getenv("REPOGRAPH_POSTGRES_DSN", "")
    if not dsn:
        raise SystemExit("REPOGRAPH_POSTGRES_DSN er ikke sat.")

    try:
        import psycopg2
    except ImportError:
        raise SystemExit("psycopg2 mangler — kør: pip install repograph[postgres]")

    conn = psycopg2.connect(dsn)
    conn.autocommit = False

    _ensure_migrations_table(conn)

    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    applied = 0

    with conn:
        for path in sql_files:
            name = path.name
            if _already_applied(conn, name):
                LOGGER.info("  skip  %s (allerede kørt)", name)
                continue
            LOGGER.info("  apply %s", name)
            sql = path.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO _schema_migrations(name, applied_at) VALUES (%s, NOW())",
                    (name,),
                )
            applied += 1

    conn.close()
    print(f"Migrationer kørt: {applied} / {len(sql_files)}")


def _ensure_migrations_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS _schema_migrations (
                name       TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
    conn.commit()


def _already_applied(conn, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM _schema_migrations WHERE name = %s", (name,))
        return cur.fetchone() is not None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run()
