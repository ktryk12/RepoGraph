from __future__ import annotations

from enum import Enum
from typing import Iterable, List


class Capability(str, Enum):
    READ_REPO = "READ_REPO"
    WRITE_REPO = "WRITE_REPO"
    RUN_TESTS = "RUN_TESTS"
    NETWORK = "NETWORK"
    WRITE_ARTIFACT = "WRITE_ARTIFACT"


def normalize_capabilities(values: Iterable[str | Capability]) -> List[str]:
    normalized: List[str] = []
    for value in values:
        if isinstance(value, Capability):
            normalized.append(value.value)
        else:
            normalized.append(str(value))
    return sorted({v for v in normalized if v})
