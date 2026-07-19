from __future__ import annotations

from repograph.postgres import tracer
from repograph.postgres.repositories.usage_logs import UsageRepository


class _Cursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, sql: str, params: tuple) -> None:
        self.calls.append((sql, params))


class _Connection:
    def __init__(self) -> None:
        self.cursor_instance = _Cursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self) -> _Cursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def test_usage_log_persists_token_economy_dimensions(monkeypatch) -> None:
    connection = _Connection()
    monkeypatch.setattr(
        "repograph.postgres.repositories.usage_logs._get_conn",
        lambda: connection,
    )

    UsageRepository().log(
        tenant_id="tenant-a",
        model_id="claude",
        target_model="claude-sonnet",
        capability="coding",
        input_tokens=400,
        output_tokens=100,
        baseline_input_tokens=1000,
        repograph_input_tokens=400,
        cache_hit=True,
        cache_saved_tokens=50,
        reused_tokens=200,
        input_price_usd=0.01,
        output_price_usd=0.02,
        repo_revision="abc123",
        session_id="session-1",
        adapter_version="v2",
    )

    assert connection.commits == 1
    assert connection.rollbacks == 0
    sql, params = connection.cursor_instance.calls[0]
    assert "saved_tokens_vs_baseline" in sql
    assert len(params) == 25
    assert 600 in params
    assert 0.03 in params


def test_verifier_marks_all_usage_for_a_task(monkeypatch) -> None:
    connection = _Connection()
    monkeypatch.setattr(
        "repograph.postgres.repositories.usage_logs._get_conn",
        lambda: connection,
    )

    UsageRepository().mark_verified(task_id="task-1", passed=True)

    sql, params = connection.cursor_instance.calls[0]
    assert "verified_success" in sql
    assert "tenant_id" not in sql
    assert params == (True, "task-1")


def test_retrieval_trace_persists_cache_and_revision_dimensions(monkeypatch) -> None:
    connection = _Connection()
    monkeypatch.setattr("repograph.postgres.tracer._get_conn", lambda: connection)

    tracer.log_retrieval_trace(
        retrieval_id="retrieval-1",
        tenant_id="tenant-a",
        query="fix auth",
        task_family="bug_localization",
        token_budget=4096,
        token_estimate=600,
        duration_ms=12,
        baseline_tokens=1200,
        saved_tokens_vs_baseline=600,
        cache_hit=True,
        cache_saved_tokens=600,
        reused_tokens=600,
        repo_revision="abc123",
        content_hash="content456",
        session_id="session-1",
        task_hint="bug_localization",
        target_model="qwen3-coder",
        adapter_version="v2",
        analysis_step_id="step-1",
        tokenizer_profile="local",
    )

    sql, params = connection.cursor_instance.calls[0]
    assert "cache_saved_tokens" in sql
    assert "repo_revision" in sql
    assert len(params) == 25
    assert params[11:16] == (1200, 600, True, 600, 600)
