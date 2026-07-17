from __future__ import annotations

from tests.fixtures.builders import make_working_set


def test_prepare_prompt_and_retry_smoke(api_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=18, task_family="targeted_refactor"),
    )
    monkeypatch.setattr(
        "repograph.working_set.builder.build",
        lambda **_: make_working_set(symbol_count=18, task_family="targeted_refactor"),
    )

    prepare = api_client.post(
        "/shared-retrieval/prepare",
        json={
            "repo_path": "/repo",
            "query": "Consumer entrypoint should hit shared retrieval first",
            "consumer": "codex",
            "output_profile": "patch",
            "target_context": 6000,
        },
    )
    retry = api_client.post(
        "/shared-retrieval/retry-pack",
        json={
            "repo_path": "/repo",
            "query": "Retry after verifier failure",
            "output_profile": "patch",
            "target_context": 6000,
            "failure_reason": "lint failed",
            "previous_diff": "@@ -1 +1 @@\n-old\n+new",
        },
    )

    assert prepare.status_code == 200
    assert retry.status_code == 200
    assert "messages" in prepare.json()
    assert retry.json()["strategy"] == "retry"


def test_analyze_plan_and_step_prepare_smoke(api_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **kwargs: make_working_set(
            symbol_count=18,
            task_family=kwargs.get("task_hint") or "targeted_refactor",
            token_budget=kwargs["token_budget"],
            query=kwargs["query"],
        ),
    )

    plan = api_client.post(
        "/shared-retrieval/analyze-plan",
        json={
            "repo_path": "/repo",
            "query": "analyze the code and understand the repo",
            "output_profile": "review",
            "target_context": 8192,
        },
    )
    step = api_client.post(
        "/shared-retrieval/prepare",
        json={
            "repo_path": "/repo",
            "query": "analyze the code and understand the repo",
            "consumer": "claude_code",
            "output_profile": "review",
            "target_context": 8192,
            "analysis_step_id": "step_high_risk_files",
        },
    )

    assert plan.status_code == 200
    assert step.status_code == 200
    assert len(plan.json()["steps"]) >= 8
    assert step.json()["analysis_step_id"] == "step_high_risk_files"
