"""
Minimal-patch specialist prompt templates.

These are templates for consumers (babyAI, agents) — RepoGraph does not call LLMs.
Each template is returned as structured text so consumers can inject them into their prompts.
"""

from __future__ import annotations

PATCH_PREAMBLES: dict[str, str] = {
    "default": (
        "You are a minimal-patch specialist. Your job is to produce the smallest correct change "
        "that solves the task. Do not refactor unrelated code. Do not add comments or logging. "
        "Output only the unified diff."
    ),
    "bug_localization": (
        "You are a minimal-patch specialist focused on bug fixing. "
        "Given the working set below, identify the single smallest change that fixes the reported bug. "
        "Prefer fixing the root cause over working around it. Output only the unified diff."
    ),
    "targeted_refactor": (
        "You are a minimal-patch specialist focused on safe refactoring. "
        "Update all call sites for the renamed/moved symbol. Do not change behaviour. "
        "Output only the unified diff."
    ),
    "targeted_test_generation": (
        "You are a minimal-patch specialist focused on test generation. "
        "Write the smallest test suite that covers the happy path and one failure case "
        "for the symbol under test. Use the existing test patterns in the working set. "
        "Output only the new test file content."
    ),
    "targeted_test_update": (
        "You are a minimal-patch specialist focused on test updates. "
        "Update only the tests that are broken by the patch. Do not rewrite passing tests. "
        "Output only the unified diff."
    ),
}

RETRY_PREAMBLE = (
    "Your previous patch attempt failed verification. "
    "The failure reason is provided below. "
    "Produce a corrected minimal patch that addresses only the failure — "
    "do not change anything else."
)


def get_preamble(task_family: str, is_retry: bool = False) -> str:
    base = PATCH_PREAMBLES.get(task_family, PATCH_PREAMBLES["default"])
    if is_retry:
        return f"{RETRY_PREAMBLE}\n\n{base}"
    return base


def format_patch_context(
    preamble: str,
    working_set_context: str,
    failure_reason: str | None = None,
    previous_diff: str | None = None,
) -> str:
    parts = [preamble, "", "## Working Set Context", working_set_context]
    if failure_reason:
        parts += ["", "## Previous Failure", failure_reason]
    if previous_diff:
        parts += ["", "## Previous Patch (failed)", f"```diff\n{previous_diff}\n```"]
    return "\n".join(parts)
