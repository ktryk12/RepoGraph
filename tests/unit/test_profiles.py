from __future__ import annotations

import pytest

from repograph.shared_retrieval.profiles import get_profile, profile_for_context, resolve_profile


@pytest.mark.parametrize(
    ("profile_name", "expected_budget"),
    [
        ("tiny", 4096),
        ("patch", 6000),
        ("review", 16384),
        ("medium", 32768),
    ],
)
def test_profiles_match_expected_context_budgets(profile_name: str, expected_budget: int) -> None:
    assert get_profile(profile_name).target_context == expected_budget


@pytest.mark.parametrize(
    ("target_context", "expected_name"),
    [
        (4096, "tiny"),
        (6000, "small"),
        (16384, "review"),
        (32768, "medium"),
    ],
)
def test_profile_for_context_maps_expected_ranges(target_context: int, expected_name: str) -> None:
    assert profile_for_context(target_context).name == expected_name


def test_resolve_profile_overrides_target_context_without_changing_strategy() -> None:
    profile = resolve_profile("patch", 4096)

    assert profile.name == "patch"
    assert profile.target_context == 4096
    assert profile.packing_strategy == "patch_first"
