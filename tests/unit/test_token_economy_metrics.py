from __future__ import annotations

from repograph.cache.keys import query_hash
from repograph.postgres.metrics import UsageTotals


def test_primary_metrics_are_calculated_per_verified_success() -> None:
    metrics = UsageTotals(
        calls=4,
        input_tokens=1200,
        output_tokens=300,
        verified_successes=2,
        baseline_input_tokens=2400,
        saved_tokens_vs_baseline=1200,
        cache_saved_tokens=200,
        reused_tokens=400,
        cache_hits=2,
        total_price_usd=0.06,
    ).as_metrics()

    assert metrics["tokens_per_verified_success"] == 750.0
    assert metrics["price_per_verified_success"] == 0.03
    assert metrics["token_reduction_pct"] == 50.0
    assert metrics["cache_hit_rate_pct"] == 50.0


def test_success_metrics_are_null_until_verification_succeeds() -> None:
    metrics = UsageTotals(input_tokens=100, output_tokens=20).as_metrics()
    assert metrics["tokens_per_verified_success"] is None
    assert metrics["price_per_verified_success"] is None


def test_cache_identity_changes_for_every_context_dimension() -> None:
    base = query_hash("fix auth", "small", 4096)
    variants = {
        query_hash("fix auth", "small", 4096, repo_revision="abc"),
        query_hash("fix auth", "small", 4096, content_hash="content"),
        query_hash("fix auth", "small", 4096, session_id="session-1"),
        query_hash("fix auth", "small", 4096, task_hint="bug_localization"),
        query_hash("fix auth", "small", 4096, target_model="claude"),
        query_hash("fix auth", "small", 4096, consumer="codex"),
        query_hash("fix auth", "small", 4096, adapter_version="v2"),
        query_hash("fix auth", "small", 4096, analysis_step_id="step-2"),
    }
    assert base not in variants
    assert len(variants) == 8
