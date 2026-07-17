from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List
import uuid

from agents.base import Agent
from babyai_shared.bus.protocol import Context, Message, MessageType


def _voice_output_enabled() -> bool:
    return os.environ.get("VOICE_OUTPUT_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


class TranslatorAgent(Agent):
    def __init__(self, agent_id: str = "translator-001") -> None:
        super().__init__(
            agent_id=agent_id,
            role="translator",
            accepts={MessageType.TRANSLATE_DECISION},
        )

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type == MessageType.TRANSLATE_DECISION:
            return self._translate_decision(message, context)
        return []

    def _translate_decision(self, message: Message, context: Context) -> List[Message]:
        decision = context.architecture_decision
        if not decision:
            return []

        explanation = self._build_explanation(decision, context.task_spec or {})

        now = datetime.now().isoformat()
        messages: List[Message] = [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="user",
            message_type=MessageType.TRANSLATION_COMPLETE,
            payload={"explanation": explanation},
            context_id=context.context_id,
            timestamp=now,
        )]

        if _voice_output_enabled():
            messages.append(Message(
                message_id=str(uuid.uuid4()),
                from_agent=self.agent_id,
                to_agent="voice-io-agent",
                message_type=MessageType.VOICE_OUTPUT,
                payload={"text": explanation},
                context_id=context.context_id,
                timestamp=now,
            ))

        return messages

    def _fmt_weight(self, w: float) -> str:
        try:
            w = float(w)
        except Exception:
            return "N/A"
        if 0.0 <= w <= 1.0:
            return f"{w*100:.0f}%"
        if 0.0 <= w <= 10.0:
            return f"{w:.1f}/10"
        return f"{w:.2f}"

    def _build_explanation(self, decision: Dict, task_spec: Dict) -> str:
        chosen = decision.get("chosen_style", "unknown")
        topology = (decision.get("topology") or {}).get("core", "unknown")

        explanation = f"# Architecture Recommendation: {str(chosen).replace('_', ' ').title()}\n\n"
        explanation += "## Rationale\n\n"

        for item in decision.get("rationale", []):
            evidence_path = item.get("evidence_path", "N/A")
            signal = item.get("signal", "N/A")
            reason = item.get("reason", "N/A")
            weight = self._fmt_weight(item.get("weight", 0.0))

            explanation += f"**{reason}**\n"
            explanation += f"- Evidence: `{evidence_path}`\n"
            explanation += f"- Signal: {signal}\n"
            explanation += f"- Weight: {weight}\n\n"

        explanation += f"## Topology: {str(topology).replace('_', ' ').title()}\n\n"

        separated = (decision.get("topology") or {}).get("separated_services", [])
        if separated:
            explanation += "### Separated Services\n"
            for svc in separated:
                explanation += f"- {svc}\n"
            explanation += "\n"

        explanation += "## Bounded Contexts\n\n"
        for bc in decision.get("bounded_contexts", []):
            stability = bc.get("stability", "unknown")
            explanation += f"- **{bc.get('name','Unnamed')}** ({stability})\n"
        explanation += "\n"

        risks = decision.get("risks", [])
        if risks:
            explanation += "## Risks and Mitigations\n\n"
            for risk in risks:
                explanation += f"RISK: {risk.get('risk','')}\n"
                explanation += f"MITIGATION: {risk.get('mitigation','')}\n\n"

        explanation += "## Verification Plan\n\n"
        explanation += "To validate this architecture, execute:\n\n"
        for signal in decision.get("verification_plan", []):
            explanation += f"- [ ] {signal}\n"
        explanation += "\n"

        stops = decision.get("stop_conditions", [])
        if stops:
            explanation += "## Stop/Change Conditions\n\n"
            for condition in stops:
                explanation += f"- {condition}\n"

        return explanation

    # ------------------------------------------------------------------
    # SkillProvider interface (Sprint A6-Adoption)
    # ------------------------------------------------------------------

    def provide(self, context: Dict[str, Any]) -> Dict[str, Any]:
        format_hint = str(context.get("target_format") or "structured")
        skill_context = (
            f"Output format: {format_hint}. "
            "Prioritise determinism over creativity. "
            "All JSON output must be stable and schema-valid."
        )
        return {
            "skill_context": skill_context,
            "skill_ids": ["translator-conventions"],
            "token_count": len(skill_context) // 4,
        }
