from __future__ import annotations

import pytest

from repograph.shared_retrieval.profiles import resolve_profile
from repograph.shared_retrieval.prompt_packer import pack

from tests.fixtures.builders import make_working_set


@pytest.mark.parametrize(
    ("profile_name", "expected_strategy", "expected_labels"),
    [
        ("tiny", "summary_first", ("src/module_0.py",)),
        ("small", "summary_first", ("src/module_0.py",)),
        ("medium", "symbol_first", ("pkg.symbol_0",)),
        ("patch", "patch_first", ("pkg.symbol_0",)),
        ("review", "symbol_first", ("pkg.symbol_0",)),
    ],
)
def test_output_profiles_produce_expected_structure(
    profile_name: str,
    expected_strategy: str,
    expected_labels: tuple[str, ...],
) -> None:
    ws = make_working_set(symbol_count=10, task_family="targeted_refactor")
    profile = resolve_profile(profile_name)

    prompt_pack = pack(ws, profile)

    assert prompt_pack.strategy == expected_strategy
    assert prompt_pack.total_tokens <= profile.target_context
    labels = {block.label for block in prompt_pack.context_blocks}
    assert any(label in labels for label in expected_labels)


def test_retry_packing_includes_failure_reason_and_previous_diff() -> None:
    ws = make_working_set(symbol_count=8, task_family="targeted_refactor")
    profile = resolve_profile("patch", 6000)

    prompt_pack = pack(
        ws,
        profile,
        failure_reason="pytest failed in tests/test_budget.py",
        previous_diff="@@ -1 +1 @@\n-old\n+new",
    )

    assert prompt_pack.strategy == "retry"
    contents = "\n".join(block.content for block in prompt_pack.context_blocks)
    assert "pytest failed in tests/test_budget.py" in contents
    assert "Previous patch (failed)" in contents
    assert "@@ -1 +1 @@" in contents


def test_prompt_packer_keeps_total_under_target_budget() -> None:
    ws = make_working_set(symbol_count=60, task_family="targeted_refactor")
    profile = resolve_profile("patch", 4096)

    prompt_pack = pack(ws, profile)

    assert prompt_pack.total_tokens <= 4096
