from __future__ import annotations

from typing import Callable, Protocol

from babyai_shared.bus.protocol import Message


MessageHandler = Callable[[Message], None]


class MessageBus(Protocol):
    """
    Minimal publish/subscribe bus interface.
    """

    def publish(self, message: Message) -> None:
        """Publish a message to the bus."""
        ...

    def subscribe(self, handler: MessageHandler, max_messages: int | None = None) -> int:
        """Consume messages and invoke handler. Returns processed count."""
        ...
