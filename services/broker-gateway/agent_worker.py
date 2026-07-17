from __future__ import annotations

from datetime import datetime
from typing import Optional
import uuid

from babyai_shared.bus.protocol import Context, Message, MessageType
from babyai_shared.bus.registry import AgentRegistry
from bus.interfaces import MessageBus, MessageHandler
from babyai_shared.storage.context_store import ContextStore, InMemoryContextStore


class AgentWorker:
    """
    Minimal worker that can be used as a bus subscriber handler.
    """

    def __init__(
        self,
        bus: MessageBus,
        registry: AgentRegistry,
        context_store: Optional[ContextStore] = None,
    ) -> None:
        self.bus = bus
        self.registry = registry
        self.context_store = context_store or InMemoryContextStore()

    def submit_user_request(self, user_input: str, task_spec: dict | None = None) -> str:
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

    def handler(self) -> MessageHandler:
        return self.handle_message

    def handle_message(self, msg: Message) -> None:
        if msg.to_agent == "user":
            return

        if msg.to_agent == "orchestrator":
            if msg.message_type == MessageType.REQUIREMENTS_COMPLETE:
                handlers = self.registry.find_handlers(MessageType.ARCHITECTURE_REQUEST)
                if not handlers:
                    return
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
            return

        agent = self.registry.get(msg.to_agent)
        if not agent:
            return

        if not agent.can_handle(msg.message_type):
            return

        context = self.context_store.load(msg.context_id)
        new_messages = agent.process(msg, context)
        for out_msg in new_messages:
            self.bus.publish(out_msg)
        self.context_store.save(context)
        self.registry.mark_processed(agent.agent_id)
