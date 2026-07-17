from __future__ import annotations

from collections import deque
from typing import Deque

from babyai_shared.bus.protocol import Message
from bus.interfaces import MessageBus, MessageHandler


class InMemoryBus(MessageBus):
    """
    Minimal in-memory bus with publish/subscribe semantics.
    """

    def __init__(self) -> None:
        self._queue: Deque[Message] = deque()

    def publish(self, message: Message) -> None:
        self._queue.append(message)

    def subscribe(self, handler: MessageHandler, max_messages: int | None = None) -> int:
        processed = 0
        limit = max_messages if max_messages is not None else float("inf")
        while self._queue and processed < limit:
            msg = self._queue.popleft()
            handler(msg)
            processed += 1
        return processed
