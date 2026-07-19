from __future__ import annotations

from repograph import container_start


def test_container_start_skips_migration_without_dsn(monkeypatch) -> None:
    monkeypatch.delenv("REPOGRAPH_POSTGRES_DSN", raising=False)
    container_start.run_migrations_with_retry()


def test_container_start_retries_postgres_then_migrates(monkeypatch) -> None:
    attempts: list[str] = []

    def fake_run(dsn: str) -> None:
        attempts.append(dsn)
        if len(attempts) < 3:
            raise ConnectionError("not ready")

    monkeypatch.setenv("REPOGRAPH_POSTGRES_DSN", "postgresql://test")
    monkeypatch.setenv("REPOGRAPH_MIGRATE_ATTEMPTS", "3")
    monkeypatch.setenv("REPOGRAPH_MIGRATE_INTERVAL", "0.1")
    monkeypatch.setattr("repograph.postgres.migrate.run", fake_run)
    monkeypatch.setattr(container_start.time, "sleep", lambda _: None)

    container_start.run_migrations_with_retry()

    assert attempts == ["postgresql://test"] * 3
