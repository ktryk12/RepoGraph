"""Output profiles — control what gets packed for each model size and task type."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class OutputProfile:
    name: str
    target_context: int
    include_summaries: bool
    include_signatures: bool
    include_code_spans: bool
    include_calls: bool
    include_tests: bool
    include_configs: bool
    max_symbols: int
    max_files: int
    packing_strategy: str   # summary_first | symbol_first | patch_first | test_first


PROFILES: dict[str, OutputProfile] = {
    "tiny": OutputProfile(
        name="tiny", target_context=4096,
        include_summaries=True, include_signatures=False, include_code_spans=False,
        include_calls=False, include_tests=False, include_configs=False,
        max_symbols=20, max_files=5, packing_strategy="summary_first",
    ),
    "small": OutputProfile(
        name="small", target_context=8192,
        include_summaries=True, include_signatures=True, include_code_spans=False,
        include_calls=True, include_tests=True, include_configs=False,
        max_symbols=40, max_files=10, packing_strategy="summary_first",
    ),
    "medium": OutputProfile(
        name="medium", target_context=32768,
        include_summaries=True, include_signatures=True, include_code_spans=True,
        include_calls=True, include_tests=True, include_configs=True,
        max_symbols=100, max_files=30, packing_strategy="symbol_first",
    ),
    "patch": OutputProfile(
        name="patch", target_context=6000,
        include_summaries=True, include_signatures=True, include_code_spans=False,
        include_calls=True, include_tests=True, include_configs=False,
        max_symbols=25, max_files=8, packing_strategy="patch_first",
    ),
    "review": OutputProfile(
        name="review", target_context=16384,
        include_summaries=True, include_signatures=True, include_code_spans=False,
        include_calls=True, include_tests=True, include_configs=True,
        max_symbols=60, max_files=20, packing_strategy="symbol_first",
    ),
}


def get_profile(name: str) -> OutputProfile:
    return PROFILES.get(name, PROFILES["small"])


def resolve_profile(name: str, target_context: int | None = None) -> OutputProfile:
    profile = get_profile(name)
    if target_context is None or target_context <= 0 or target_context == profile.target_context:
        return profile
    return replace(profile, target_context=target_context)


def profile_for_context(target_context: int) -> OutputProfile:
    if target_context <= 4096:
        return PROFILES["tiny"]
    if target_context <= 8192:
        return PROFILES["small"]
    if target_context <= 16384:
        return PROFILES["review"]
    return PROFILES["medium"]
