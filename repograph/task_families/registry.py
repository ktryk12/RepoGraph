"""Task family registry — central lookup and default resolution."""

from __future__ import annotations

from .families import ALL_FAMILIES, TaskFamily

_REGISTRY: dict[str, TaskFamily] = {f.name: f for f in ALL_FAMILIES}


def get(name: str) -> TaskFamily | None:
    return _REGISTRY.get(name)


def get_or_default(name: str) -> TaskFamily:
    return _REGISTRY.get(name, _REGISTRY["symbol_lookup"])


def list_all() -> list[TaskFamily]:
    return list(ALL_FAMILIES)


def names() -> list[str]:
    return [f.name for f in ALL_FAMILIES]


def defaults_for(name: str) -> dict:
    """Return retrieval defaults for a task family."""
    family = get_or_default(name)
    return {
        "coarse_limit": family.coarse_limit,
        "expand_limit": family.expand_limit,
        "token_budget": family.token_budget,
    }
