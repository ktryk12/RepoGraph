from __future__ import annotations

from typing import Any, Iterable

try:
    from confluent_kafka import cimpl
except Exception:  # pragma: no cover - optional dependency
    cimpl = None  # type: ignore[assignment]


def is_kafka_exception(exc: BaseException) -> bool:
    if cimpl is None:
        return False
    try:
        return isinstance(exc, cimpl.KafkaException)
    except Exception:
        return False


def kafka_exception_code(exc: BaseException) -> int | None:
    candidate = None
    args = getattr(exc, "args", ())
    if args:
        first = args[0]
        if hasattr(first, "code"):
            candidate = first
    if candidate is None and hasattr(exc, "code"):
        candidate = exc
    if candidate is None:
        return None
    try:
        return int(candidate.code())  # type: ignore[no-any-return]
    except Exception:
        return None


def retryable_kafka_error_codes() -> set[int]:
    codes: set[int] = set()
    # Kafka broker error for unknown topic/partition.
    codes.add(3)
    if cimpl is None:
        return codes
    kafka_error = cimpl.KafkaError
    for name in (
        "UNKNOWN_TOPIC_OR_PART",
        "_TRANSPORT",
        "_ALL_BROKERS_DOWN",
        "_TIMED_OUT",
        "_TIMED_OUT_QUEUE",
    ):
        value = getattr(kafka_error, name, None)
        if isinstance(value, int):
            codes.add(value)
    return codes


def is_retryable_kafka_exception(exc: BaseException) -> bool:
    code = kafka_exception_code(exc)
    if code is not None and code in retryable_kafka_error_codes():
        return True
    text = str(exc).strip().lower()
    retryable_fragments: Iterable[str] = (
        "unknown_topic_or_part",
        "unknown topic",
        "connection refused",
        "transport",
        "all brokers down",
        "broker transport failure",
        "failed to resolve",
        "connection error",
    )
    return any(fragment in text for fragment in retryable_fragments)

