from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IntentRecord:
    decision_id: str
    context_id: str
    policy_preset: str
    user_prompt: str
    template_id: str = "auto"


@dataclass(frozen=True)
class ReadyRecord:
    decision_id: str
    context_id: str
    policy_preset: str
    truth_pack_alias: str
    user_override_ref: str
    explanation_text: str
    override_hash: str
