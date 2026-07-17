"""
Message router with agent registry.

ASCII-only output for cross-platform compatibility.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
import uuid

from babyai_shared.bus.protocol import Context, Message, MessageType
from agents.registry import AgentRegistry
from babyai_shared.storage.context_store import ContextStore, InMemoryContextStore


class SimpleOrchestrator:
    """
    Message router with registry-based agent discovery.

    Responsibilities:
    - Route messages to capable agents
    - Manage context lifecycle
    - Handle errors gracefully
    - Format user-facing responses
    """

    def __init__(
        self,
        registry: AgentRegistry,
        context_store: Optional[ContextStore] = None,
    ) -> None:
        self.registry = registry
        self.context_store = context_store or InMemoryContextStore()

    def handle_user_request(self, user_input: str, task_spec: Dict[str, Any]) -> str:
        """
        Main entry point.

        Flow:
        1. Create context
        2. Find requirements agent (if any), else architect agent
        3. Route initial request
        4. Process message chain
        5. Return explanation or error
        """
        context_id = str(uuid.uuid4())
        context = Context(
            context_id=context_id,
            user_request=user_input,
            task_spec=task_spec,
        )
        self.context_store.save(context)

        use_requirements = not (isinstance(task_spec, dict) and task_spec)

        if use_requirements:
            req_handlers = self.registry.find_handlers(MessageType.USER_REQUEST)
            if req_handlers:
                target_agent_id = req_handlers[0].agent_id
                msg_type = MessageType.USER_REQUEST
                payload = {"text": user_input, "reply_to": "orchestrator"}
            else:
                use_requirements = False

        if not use_requirements:
            arch_handlers = self.registry.find_handlers(MessageType.ARCHITECTURE_REQUEST)
            if not arch_handlers:
                return "[ERROR] No architecture agent registered"
            target_agent_id = arch_handlers[0].agent_id
            msg_type = MessageType.ARCHITECTURE_REQUEST
            payload = {}

        msg = Message(
            message_id=str(uuid.uuid4()),
            from_agent="user",
            to_agent=target_agent_id,
            message_type=msg_type,
            payload=payload,
            context_id=context_id,
            timestamp=datetime.now().isoformat(),
        )

        messages_to_process = [msg]
        user_response: Optional[str] = None

        max_iterations = 20
        iteration = 0

        while messages_to_process and iteration < max_iterations:
            current_msg = messages_to_process.pop(0)

            if current_msg.message_type == MessageType.ARCHITECTURE_VALIDATION_FAILED:
                errors = current_msg.payload.get("errors", [])
                error_text = self._format_validation_errors(errors)
                return f"[VALIDATION FAILED]\n\n{error_text}"

            if current_msg.to_agent == "user":
                user_response = current_msg.payload.get("explanation")
                break

            if current_msg.to_agent == "orchestrator":
                if current_msg.message_type == MessageType.REQUIREMENTS_COMPLETE:
                    arch_handlers = self.registry.find_handlers(MessageType.ARCHITECTURE_REQUEST)
                    if not arch_handlers:
                        return "[ERROR] No architecture agent registered"
                    next_msg = Message(
                        message_id=str(uuid.uuid4()),
                        from_agent="orchestrator",
                        to_agent=arch_handlers[0].agent_id,
                        message_type=MessageType.ARCHITECTURE_REQUEST,
                        payload={},
                        context_id=current_msg.context_id,
                        timestamp=datetime.now().isoformat(),
                    )
                    messages_to_process.append(next_msg)
                    iteration += 1
                    continue

            agent = self.registry.get(current_msg.to_agent)
            if not agent:
                print(f"[WARN] Unknown agent: {current_msg.to_agent}")
                iteration += 1
                continue

            if not agent.can_handle(current_msg.message_type):
                print(f"[WARN] Agent {agent.agent_id} cannot handle {current_msg.message_type}")
                iteration += 1
                continue

            context = self.context_store.load(current_msg.context_id)

            try:
                new_messages = agent.process(current_msg, context)
                messages_to_process.extend(new_messages)

                self.context_store.save(context)
                self.registry.mark_processed(agent.agent_id)

            except Exception as e:
                error_msg = f"[ERROR] Agent {agent.agent_id} failed: {e}"
                print(error_msg)
                self.registry.mark_failed(agent.agent_id, str(e))
                return error_msg

            iteration += 1

        if iteration >= max_iterations:
            return "[ERROR] Max iterations reached - possible infinite loop"

        return user_response or "[ERROR] No response generated"

    def _format_validation_errors(self, errors: list[Dict[str, Any]]) -> str:
        if not errors:
            return "Unknown validation error"

        formatted = []
        for err in errors:
            if isinstance(err, dict):
                code = err.get("code", "UNKNOWN")
                path = err.get("path", "")
                msg = err.get("msg", "No message")
                severity = err.get("severity", "error")

                formatted.append(f"[{str(severity).upper()}] {code}")
                if path:
                    formatted.append(f"  Path: {path}")
                formatted.append(f"  {msg}")
                formatted.append("")
            else:
                formatted.append(str(err))
                formatted.append("")

        return "\n".join(formatted).rstrip()
