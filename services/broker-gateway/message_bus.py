from __future__ import annotations

from typing import Protocol, Iterable

from babyai_shared.bus.protocol import Message


class MessageBus(Protocol):
    """
    Minimal message bus interface.

    Implementations:
    - InMemoryBus (in-process)
    - KafkaBus (distributed, later)
    """

    def publish(self, message: Message) -> None:
        """Publish a message to the bus."""
        ...

    def consume(self, max_messages: int = 1) -> Iterable[Message]:
        """Consume up to max_messages from the bus."""
        ...
