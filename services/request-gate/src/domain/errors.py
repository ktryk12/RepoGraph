from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DomainError:
    code: str
    message: str
    field: str | None = None

