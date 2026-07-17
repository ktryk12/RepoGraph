from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Question:
    question_id: str
    prompt: str
    required: bool = True


@dataclass(frozen=True)
class AnswerSet:
    decision_id: str
    answers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TruthOverrideDraft:
    decision_id: str
    context_id: str
    policy_preset: str
    truth_pack_alias: str
    override_hash: str
    override_yaml: str
    explanation_text: str
    user_override_ref: str | None = None
