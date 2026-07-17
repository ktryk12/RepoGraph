from __future__ import annotations

import logging
import threading
import time
import importlib

import pytest

planner_main = importlib.import_module("planner.main")


class _FakeKafkaError:
    def __init__(self, code: int, message: str) -> None:
        self._code = int(code)
        self._message = str(message)

    def code(self) -> int:
        return int(self._code)

    def __str__(self) -> str:
        return str(self._message)


class _FakeKafkaException(Exception):
    pass


class _RetryThenSuccessConsumer:
    def __init__(self, **_: object) -> None:
        self.allow_success = threading.Event()
        self.retry_seen = threading.Event()
        self.closed = False

    def run_once(self) -> int:
        if not self.allow_success.is_set():
            self.retry_seen.set()
            raise _FakeKafkaException(_FakeKafkaError(3, "UNKNOWN_TOPIC_OR_PART"))
        return 0

    def close(self) -> None:
        self.closed = True


def _wait_until(predicate, *, timeout_seconds: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_planner_runtime_retries_until_kafka_topics_exist(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    try:
        from fastapi.testclient import TestClient
    except Exception:  # pragma: no cover - optional dependency
        pytest.skip("fastapi test client is unavailable")

    monkeypatch.setattr(planner_main, "KafkaPlannerConsumer", _RetryThenSuccessConsumer)
    monkeypatch.setattr(
        planner_main.kafka_retry,
        "is_kafka_exception",
        lambda exc: isinstance(exc, _FakeKafkaException),
    )
    monkeypatch.setenv("PLANNER_KAFKA_RETRY_INITIAL_BACKOFF_SECONDS", "0.01")
    monkeypatch.setenv("PLANNER_KAFKA_RETRY_MAX_BACKOFF_SECONDS", "0.02")
    monkeypatch.setenv("PLANNER_IDLE_SLEEP_SECONDS", "0.01")

    caplog.set_level(logging.WARNING)
    app = planner_main.create_app()
    runtime = app.state.runtime

    with TestClient(app) as client:
        assert _wait_until(lambda: runtime._consumer.retry_seen.is_set())  # type: ignore[attr-defined]
        unhealthy = client.get("/health")
        assert unhealthy.status_code == 503

        runtime._consumer.allow_success.set()  # type: ignore[attr-defined]
        assert _wait_until(lambda: client.get("/health").status_code == 200)

    assert any("waiting for Kafka topics" in record.getMessage() for record in caplog.records)
    assert not any("planner_loop_failed" in record.getMessage() for record in caplog.records)
