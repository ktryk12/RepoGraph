from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
import uuid

from babyai_shared.bus.protocol import Context, Message, MessageType
from babyai_shared.bus.registry import AgentRegistry
from bus.message_bus import MessageBus
from babyai_shared.storage.context_store import ContextStore, InMemoryContextStore


@dataclass
class WorkerResult:
    processed: int = 0
    user_messages: int = 0


class MessageWorker:
    """
    Worker that consumes messages from a bus and dispatches to agents.

    This mirrors the orchestrator loop but uses a bus as transport.
    """

    def __init__(
        self,
        bus: MessageBus,
        registry: AgentRegistry,
        context_store: Optional[ContextStore] = None,
        user_sink: Optional[Callable[[Message], None]] = None,
    ) -> None:
        self.bus = bus
        self.registry = registry
        self.context_store = context_store or InMemoryContextStore()
        self.user_sink = user_sink

    def submit_user_request(self, user_input: str, task_spec: dict | None = None) -> str:
        """
        Convenience method: create context + seed a USER_REQUEST or ARCHITECTURE_REQUEST.
        """
        context_id = str(uuid.uuid4())
        context = Context(
            context_id=context_id,
            user_request=user_input,
            task_spec=task_spec or {},
        )
        self.context_store.save(context)

        if task_spec:
            msg_type = MessageType.ARCHITECTURE_REQUEST
            payload = {}
            handlers = self.registry.find_handlers(MessageType.ARCHITECTURE_REQUEST)
        else:
            msg_type = MessageType.USER_REQUEST
            payload = {"text": user_input}
            handlers = self.registry.find_handlers(MessageType.USER_REQUEST)

        if not handlers:
            raise RuntimeError("No handler registered for initial request")

        msg = Message(
            message_id=str(uuid.uuid4()),
            from_agent="user",
            to_agent=handlers[0].agent_id,
            message_type=msg_type,
            payload=payload,
            context_id=context_id,
            timestamp=datetime.now().isoformat(),
        )
        self.bus.publish(msg)
        return context_id

    def run_once(self, max_messages: int = 1) -> WorkerResult:
        result = WorkerResult()

        for msg in self.bus.consume(max_messages=max_messages):
            result.processed += 1

            if msg.to_agent == "user":
                result.user_messages += 1
                if self.user_sink:
                    self.user_sink(msg)
                continue

            if msg.to_agent == "orchestrator":
                if msg.message_type == MessageType.REQUIREMENTS_COMPLETE:
                    handlers = self.registry.find_handlers(MessageType.ARCHITECTURE_REQUEST)
                    if not handlers:
                        continue
                    next_msg = Message(
                        message_id=str(uuid.uuid4()),
                        from_agent="orchestrator",
                        to_agent=handlers[0].agent_id,
                        message_type=MessageType.ARCHITECTURE_REQUEST,
                        payload={},
                        context_id=msg.context_id,
                        timestamp=datetime.now().isoformat(),
                    )
                    self.bus.publish(next_msg)
                continue

            agent = self.registry.get(msg.to_agent)
            if not agent:
                continue

            if not agent.can_handle(msg.message_type):
                continue

            context = self.context_store.load(msg.context_id)

            new_messages = agent.process(msg, context)
            for out_msg in new_messages:
                self.bus.publish(out_msg)

            self.context_store.save(context)
            self.registry.mark_processed(agent.agent_id)

        return result

    def run_until_idle(self, max_iterations: int = 1000) -> WorkerResult:
        result = WorkerResult()
        iteration = 0
        while iteration < max_iterations:
            batch = self.run_once(max_messages=50)
            result.processed += batch.processed
            result.user_messages += batch.user_messages
            if batch.processed == 0:
                break
            iteration += 1
        return result
