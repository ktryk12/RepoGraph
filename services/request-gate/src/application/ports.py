from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from domain.models import CanonicalLifecycleRequestedEvent, DecisionRequest


class DedupeStore(Protocol):
    def claim(self, *, key: str, ttl_seconds: int) -> bool:
        ...


class LifecyclePublisher(Protocol):
    def publish(self, event: CanonicalLifecycleRequestedEvent) -> None:
        ...


class DlqPublisher(Protocol):
    def publish(self, *, reason_code: str, message: str, payload: Mapping[str, Any]) -> None:
        ...


@dataclass(frozen=True)
class PolicyValidatorResult:
    allowed: bool
    reason_code: str | None = None
    message: str | None = None
    metadata: Mapping[str, Any] | None = None


class PolicyValidatorPort(Protocol):
    def validate_request(self, request: DecisionRequest) -> PolicyValidatorResult:
        ...

