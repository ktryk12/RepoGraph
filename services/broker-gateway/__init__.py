"""Message bus layer for agent runtime."""

try:
    from bus.message_bus import MessageBus
    from bus.in_memory_bus import InMemoryBus
except ImportError:
    MessageBus = None  # type: ignore[assignment,misc]
    InMemoryBus = None  # type: ignore[assignment,misc]

__all__ = ["MessageBus", "InMemoryBus"]
