from __future__ import annotations

from typing import Any, Mapping, Protocol


class OverrideStore(Protocol):
    def write_override(self, *, override_hash: str, override_yaml: str) -> str:
        ...


class QuestionsPublisher(Protocol):
    def publish_questions(self, payload: Mapping[str, Any]) -> None:
        ...


class ReadyPublisher(Protocol):
    def publish_ready(self, payload: Mapping[str, Any]) -> None:
        ...


class DlqPublisher(Protocol):
    def publish_dlq(self, *, reason_code: str, message: str, payload: Mapping[str, Any]) -> None:
        ...
