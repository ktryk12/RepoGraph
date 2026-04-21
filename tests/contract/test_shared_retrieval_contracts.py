from __future__ import annotations

import json
from pathlib import Path

from repograph.mcp_server import server as mcp_server
from repograph.shared_retrieval.adapters import format_for_consumer
from repograph.shared_retrieval import SharedRetrievalRequest, prepare_task_context

from tests.fixtures.builders import make_working_set


def _golden(name: str) -> dict:
    root = Path(__file__).resolve().parents[1] / "golden"
    return json.loads((root / name).read_text(encoding="utf-8"))


def test_prepare_task_context_response_shape_matches_contract(
    sample_request,
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=14, token_budget=sample_request.target_context),
    )

    response = prepare_task_context(sample_request, fake_store).model_dump()
    shape = _golden("prepare_task_context_shape.json")

    assert set(response) == set(shape["top_level"])
    assert set(response["prompt_pack"]) == set(shape["prompt_pack"])
    assert set(response["verification_plan"]) == set(shape["verification_plan"])


def test_build_prompt_pack_response_shape_matches_contract(api_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=10),
    )

    response = api_client.post(
        "/shared-retrieval/prompt-pack",
        json={"repo_path": "/repo", "query": "Inspect pack", "target_context": 4096, "output_profile": "small"},
    )

    assert response.status_code == 200
    payload = response.json()
    shape = _golden("prompt_pack_shape.json")
    assert set(payload) == set(shape["keys"])
    assert payload["context_blocks"]
    assert set(payload["context_blocks"][0]) == set(shape["context_block_keys"])


def test_build_retry_pack_response_shape_matches_contract(api_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=12, task_family="targeted_refactor"),
    )
    monkeypatch.setattr(
        "repograph.working_set.builder.build",
        lambda **_: make_working_set(symbol_count=12, task_family="targeted_refactor"),
    )

    response = api_client.post(
        "/shared-retrieval/retry-pack",
        json={
            "repo_path": "/repo",
            "query": "Retry after verifier failure",
            "output_profile": "patch",
            "target_context": 6000,
            "failure_reason": "pytest failed",
            "previous_diff": "@@ -1 +1 @@\n-old\n+new",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy"] == "retry"
    assert any(block["role"] == "retry" for block in payload["context_blocks"])


def test_mcp_outputs_match_api_contracts(fake_store, monkeypatch) -> None:
    monkeypatch.setattr(mcp_server, "TENANT_ID", "tenant-a")
    monkeypatch.setattr(
        "repograph.graph.get_graph_store",
        lambda **_: fake_store,
    )
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=10),
    )
    monkeypatch.setattr(
        "repograph.working_set.builder.build",
        lambda **_: make_working_set(symbol_count=10, task_family="targeted_refactor"),
    )

    prepared = mcp_server.prepare_task_context("/repo", "Investigate bug", consumer="generic")
    prompt_pack = mcp_server.build_prompt_pack("/repo", "Investigate bug")
    retry_pack = mcp_server.build_retry_pack("/repo", "Investigate bug", failure_reason="pytest failed")

    assert "prompt_pack" in prepared
    assert set(prompt_pack) == set(_golden("prompt_pack_shape.json")["keys"])
    assert retry_pack["strategy"] == "retry"


def test_backward_compatibility_for_existing_shared_retrieval_endpoints(api_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=9),
    )

    body = {
        "repo_path": "/repo",
        "query": "Find relevant context",
        "consumer": "generic",
        "target_context": 4096,
        "output_profile": "small",
    }
    prepare_response = api_client.post("/shared-retrieval/prepare", json=body)
    ws_response = api_client.post("/shared-retrieval/working-set", json=body)
    prompt_response = api_client.post("/shared-retrieval/prompt-pack", json=body)

    assert prepare_response.status_code == 200
    assert ws_response.status_code == 200
    assert prompt_response.status_code == 200
    assert "retrieval_trace_id" in prepare_response.json()
    assert "token_budget" in ws_response.json()
    assert "context_blocks" in prompt_response.json()


def test_claude_code_consumer_contract_preserves_flat_prompt_and_full_envelope(
    api_client,
    fake_store,
    fake_redis,
    fake_tracer,
    monkeypatch,
) -> None:
    req = SharedRetrievalRequest(
        repo_path="/repo",
        query="Preserve the RepoGraph envelope for Claude Code",
        consumer="claude_code",
        output_profile="patch",
        target_context=6000,
    )
    monkeypatch.setattr(
        "repograph.shared_retrieval.gateway.build_working_set",
        lambda **_: make_working_set(symbol_count=12, token_budget=req.target_context),
    )

    response = prepare_task_context(req, fake_store)
    payload = format_for_consumer(response, "claude_code")

    assert payload["prompt"]
    assert payload["prompt_pack"] == response.prompt_pack.model_dump()
    assert payload["working_set"] == response.working_set
    assert payload["verification_plan"] == response.verification_plan.model_dump()
    assert payload["retrieval_trace_id"] == response.retrieval_trace_id
    assert payload["cache"] == response.cache.model_dump()

    api_response = api_client.post(
        "/shared-retrieval/prepare",
        json={
            "repo_path": "/repo",
            "query": "Preserve the RepoGraph envelope for Claude Code",
            "consumer": "claude_code",
            "output_profile": "patch",
            "target_context": 6000,
        },
    )

    assert api_response.status_code == 200
    api_payload = api_response.json()
    assert api_payload["prompt"]
    assert "prompt_pack" in api_payload
    assert "working_set" in api_payload
    assert "verification_plan" in api_payload
    assert "retrieval_trace_id" in api_payload
    assert "cache" in api_payload
