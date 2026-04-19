"""Rule-based task classifier — maps a free-text query to one of 9 task families."""

from __future__ import annotations

import re

TASK_FAMILIES = (
    "symbol_lookup",
    "file_to_symbol_map",
    "bug_localization",
    "call_chain_reasoning",
    "blast_radius_analysis",
    "targeted_refactor",
    "test_impact_lookup",
    "targeted_test_generation",
    "config_dependency_reasoning",
)

_RULES: list[tuple[re.Pattern[str], str]] = [
    # blast radius / impact
    (re.compile(r"\b(blast.?radius|impact|affects?|downstream|dependents?|who.?uses?)\b", re.I), "blast_radius_analysis"),
    # call chain
    (re.compile(r"\b(call.?chain|call.?graph|calls?|callers?|callees?|invokes?|traces?)\b", re.I), "call_chain_reasoning"),
    # test generation
    (re.compile(r"\b(generat.{0,10}test|write.{0,10}test|test.{0,10}for|unit.?test)\b", re.I), "targeted_test_generation"),
    # test impact
    (re.compile(r"\b(which.{0,15}tests?|tests?.{0,15}affected|test.?impact|break.{0,10}tests?)\b", re.I), "test_impact_lookup"),
    # refactor
    (re.compile(r"\b(refactor|rename|extract|move|restructure|clean.?up)\b", re.I), "targeted_refactor"),
    # bug localization
    (re.compile(r"\b(bug|error|exception|crash|fail|wrong|broken|why.{0,15}not.{0,15}work)\b", re.I), "bug_localization"),
    # config
    (re.compile(r"\b(config|env.?var|setting|feature.?flag|environment|yaml|toml|dotenv)\b", re.I), "config_dependency_reasoning"),
    # file map
    (re.compile(r"\b(what.{0,20}in.{0,10}file|file.{0,10}content|symbols?.{0,10}in|overview.{0,10}file)\b", re.I), "file_to_symbol_map"),
    # symbol lookup (catch-all for "where is", "find", "define", "what is")
    (re.compile(r"\b(where.{0,10}is|find|defin|locate|what.{0,10}is|show.{0,10}me)\b", re.I), "symbol_lookup"),
]


def classify(query: str, hint: str | None = None) -> str:
    """Return the most likely task family for a query.

    If `hint` is provided and is a valid task family it is used directly.
    """
    if hint and hint in TASK_FAMILIES:
        return hint

    for pattern, family in _RULES:
        if pattern.search(query):
            return family

    return "symbol_lookup"
