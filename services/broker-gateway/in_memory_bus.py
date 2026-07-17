from __future__ import annotations

from collections import deque
from typing import Deque, Iterable, List

from babyai_shared.bus.protocol import Message


class InMemoryBus:
    """
    In-memory message bus.

    Intended for:
    - Local development
    - Unit/integration tests
    - Single-process runtime
    """

    def __init__(self) -> None:
        self._queue: Deque[Message] = deque()

    def publish(self, message: Message) -> None:
        self._queue.append(message)

    def consume(self, max_messages: int = 1) -> Iterable[Message]:
        msgs: List[Message] = []
        for _ in range(max(1, max_messages)):
            if not self._queue:
                break
            msgs.append(self._queue.popleft())
        return msgs

    def is_empty(self) -> bool:
        return not self._queue

    def size(self) -> int:
        return len(self._queue)
