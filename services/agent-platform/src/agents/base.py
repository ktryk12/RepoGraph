from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set

from babyai_shared.bus.protocol import Context, Message, MessageType


@dataclass
class Agent:
    agent_id: str
    role: str
    accepts: Optional[Set[MessageType]] = None
    _skill_prefix: str = field(default="", init=False, repr=False)

    def process(self, message: Message, context: Context) -> List[Message]:
        raise NotImplementedError

    def can_handle(self, message_type: MessageType) -> bool:
        if not self.accepts:
            return True
        return message_type in self.accepts

    def prepend_skill_context(self, context: str) -> None:
        text = str(context or "").strip()
        if not text:
            self._skill_prefix = ""
            return
        self._skill_prefix = (
            "=== DOMAIN KNOWLEDGE (reference only) ===\n"
            + text
            + "\n=== END DOMAIN KNOWLEDGE ===\n\n"
        )

    def apply_skill_prefix(self, prompt: str) -> str:
        prefix = str(self._skill_prefix or "").strip()
        if not prefix:
            return str(prompt or "")
        return f"{prefix}\n{str(prompt or '')}".strip()
