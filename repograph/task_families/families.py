"""Definitions for all 9 task families."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TaskFamily:
    name: str
    description: str
    coarse_limit: int
    expand_limit: int
    token_budget: int
    # Graph edges the structural expander should prioritise for this family
    priority_edges: tuple[str, ...] = field(default_factory=tuple)
    # Brief instruction injected at the top of a prompt context
    prompt_preamble: str = ""


ALL_FAMILIES: tuple[TaskFamily, ...] = (
    TaskFamily(
        name="symbol_lookup",
        description="Find where a symbol is defined, what it does, and who uses it.",
        coarse_limit=20,
        expand_limit=40,
        token_budget=2048,
        priority_edges=("DEFINES", "IN_FILE", "AT_LINE"),
        prompt_preamble=(
            "The following context shows the definition and immediate neighbourhood "
            "of the requested symbol. Use it to answer where it is defined and what it does."
        ),
    ),
    TaskFamily(
        name="file_to_symbol_map",
        description="List and summarise all symbols defined in a file.",
        coarse_limit=60,
        expand_limit=60,
        token_budget=3000,
        priority_edges=("DEFINES", "IN_FILE"),
        prompt_preamble=(
            "The following context lists all symbols in the requested file. "
            "Use it to give an overview of the file's responsibilities."
        ),
    ),
    TaskFamily(
        name="bug_localization",
        description="Identify which symbols are most likely responsible for a reported bug.",
        coarse_limit=30,
        expand_limit=60,
        token_budget=4096,
        priority_edges=("CALLS", "IN_FILE", "INHERITS"),
        prompt_preamble=(
            "The following context contains symbols related to the reported issue, "
            "their callers, and call chains. Use it to localise the likely fault."
        ),
    ),
    TaskFamily(
        name="call_chain_reasoning",
        description="Trace how a call propagates through the codebase.",
        coarse_limit=20,
        expand_limit=80,
        token_budget=4096,
        priority_edges=("CALLS",),
        prompt_preamble=(
            "The following context traces the call chain from the entry point downwards. "
            "Follow the CALLS edges to reason about execution flow."
        ),
    ),
    TaskFamily(
        name="blast_radius_analysis",
        description="Determine what is affected if a symbol changes.",
        coarse_limit=10,
        expand_limit=80,
        token_budget=4096,
        priority_edges=("CALLS",),
        prompt_preamble=(
            "The following context shows all symbols that transitively depend on "
            "the changed symbol. Use it to assess the blast radius of the change."
        ),
    ),
    TaskFamily(
        name="targeted_refactor",
        description="Gather context needed to safely rename or restructure a symbol.",
        coarse_limit=20,
        expand_limit=60,
        token_budget=4096,
        priority_edges=("CALLS", "DEFINES", "IMPORTS"),
        prompt_preamble=(
            "The following context contains the symbol to refactor, all its callers, "
            "and relevant imports. Use it to produce a minimal, safe patch."
        ),
    ),
    TaskFamily(
        name="test_impact_lookup",
        description="Find which tests cover a symbol and would be affected by a change.",
        coarse_limit=20,
        expand_limit=50,
        token_budget=3000,
        priority_edges=("TESTS", "CALLS"),
        prompt_preamble=(
            "The following context lists test symbols that cover the changed symbol "
            "and their imports. Use it to identify which tests must be re-run or updated."
        ),
    ),
    TaskFamily(
        name="targeted_test_generation",
        description="Collect context needed to write tests for a specific symbol.",
        coarse_limit=15,
        expand_limit=40,
        token_budget=3000,
        priority_edges=("DEFINES", "CALLS", "IN_FILE"),
        prompt_preamble=(
            "The following context shows the symbol under test, its signature, "
            "its callees, and any existing test patterns. Use it to write targeted tests."
        ),
    ),
    TaskFamily(
        name="config_dependency_reasoning",
        description="Understand how configuration affects a module or service.",
        coarse_limit=20,
        expand_limit=40,
        token_budget=2048,
        priority_edges=("CONFIGURES", "BELONGS_TO_SERVICE"),
        prompt_preamble=(
            "The following context shows config nodes and the modules they configure. "
            "Use it to reason about environment variables, feature flags, or settings."
        ),
    ),
)
