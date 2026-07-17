from __future__ import annotations

from typing import Any, Mapping, Protocol


class TaskSpecStore(Protocol):
    def store(self, *, task_spec: Mapping[str, Any]) -> str:
        ...


class DecisionRequestedPublisher(Protocol):
    def publish(self, payload: Mapping[str, Any]) -> None:
        ...


class DlqPublisher(Protocol):
    def publish_dlq(self, *, reason_code: str, message: str, payload: Mapping[str, Any]) -> None:
        ...
