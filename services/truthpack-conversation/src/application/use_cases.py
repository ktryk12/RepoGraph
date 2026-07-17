from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from truthpack_conversation.domain import AnswerSet, TruthOverrideDraft, build_override, generate_questions, question_to_payload

from .ports import DlqPublisher, OverrideStore, QuestionsPublisher, ReadyPublisher


@dataclass(frozen=True)
class IntentEnvelope:
    decision_id: str
    context_id: str
    policy_preset: str
    user_prompt: str


class GenerateQuestions:
    def execute(self, *, intent: IntentEnvelope) -> list[dict[str, Any]]:
        questions = generate_questions(intent=intent.user_prompt, policy_preset=intent.policy_preset)
        return [question_to_payload(question) for question in questions]


class BuildOverride:
    def __init__(self, *, override_store: OverrideStore, truth_pack_alias: str = "layered_default") -> None:
        self._override_store = override_store
        self._truth_pack_alias = str(truth_pack_alias).strip() or "layered_default"

    def execute(self, *, intent: IntentEnvelope, answers: AnswerSet) -> TruthOverrideDraft:
        _, override_yaml, explanation_text, override_hash = build_override(
            decision_id=intent.decision_id,
            context_id=intent.context_id,
            intent=intent.user_prompt,
            answers=answers,
            policy_preset=intent.policy_preset,
        )
        ref = self._override_store.write_override(override_hash=override_hash, override_yaml=override_yaml)
        return TruthOverrideDraft(
            decision_id=intent.decision_id,
            context_id=intent.context_id,
            policy_preset=intent.policy_preset,
            truth_pack_alias=self._truth_pack_alias,
            override_hash=override_hash,
            override_yaml=override_yaml,
            explanation_text=explanation_text,
            user_override_ref=ref,
        )


class TruthpackConversationService:
    # Topic for claim.detected events (read by KafkaTruthpackConversationConsumer)
    _claim_topic: str = "claim.detected"

    def __init__(
        self,
        *,
        generate_questions: GenerateQuestions,
        build_override: BuildOverride,
        questions_publisher: QuestionsPublisher,
        ready_publisher: ReadyPublisher,
        dlq_publisher: DlqPublisher,
    ) -> None:
        self._generate_questions = generate_questions
        self._build_override = build_override
        self._questions_publisher = questions_publisher
        self._ready_publisher = ready_publisher
        self._dlq_publisher = dlq_publisher
        self._intent_cache: dict[str, IntentEnvelope] = {}

    def handle_intent(self, payload: Mapping[str, Any]) -> None:
        try:
            intent = _parse_intent(payload)
            questions = self._generate_questions.execute(intent=intent)
            self._intent_cache[intent.decision_id] = intent
            self._questions_publisher.publish_questions(
                {
                    "decision_id": intent.decision_id,
                    "context_id": intent.context_id,
                    "policy_preset": intent.policy_preset,
                    "questions": questions,
                }
            )
        except Exception as exc:
            self._dlq_publisher.publish_dlq(
                reason_code="INTENT_INVALID",
                message=str(exc),
                payload={"source": "decision.intent", "raw": dict(payload or {})},
            )

    def handle_claim(self, payload: Mapping[str, Any]) -> None:
        """
        Convert a claim.detected event into an IntentEnvelope and run the
        standard 7-question research flow. Claim becomes the user_prompt.
        """
        try:
            claim_id   = _required_text(payload, "claim_id") or _required_text(payload, "claim_id")
            raw_text   = _required_text(payload, "raw_text")
            platform   = _required_text(payload, "platform") or "unknown"
            if not raw_text:
                raise ValueError("raw_text is required in claim.detected")
            if not claim_id:
                import uuid
                claim_id = str(uuid.uuid4())
            intent = IntentEnvelope(
                decision_id  = claim_id,
                context_id   = claim_id,
                policy_preset= "fact_check",
                user_prompt  = f"[{platform.upper()}] {raw_text}",
            )
            questions = self._generate_questions.execute(intent=intent)
            self._intent_cache[intent.decision_id] = intent
            self._questions_publisher.publish_questions({
                "decision_id":  intent.decision_id,
                "context_id":   intent.context_id,
                "policy_preset": intent.policy_preset,
                "questions":    questions,
                "source":       "claim.detected",
                "platform":     platform,
            })
        except Exception as exc:
            self._dlq_publisher.publish_dlq(
                reason_code="CLAIM_INVALID",
                message=str(exc),
                payload={"source": "claim.detected", "raw": dict(payload or {})},
            )

    def handle_answers(self, payload: Mapping[str, Any]) -> None:
        try:
            decision_id = _required_text(payload, "decision_id")
            if not decision_id:
                raise ValueError("decision_id is required")
            intent = self._intent_cache.get(decision_id)
            if intent is None:
                raise ValueError(f"intent not found for decision_id={decision_id}")
            answers_value = payload.get("answers")
            if not isinstance(answers_value, Mapping):
                raise ValueError("answers must be an object")
            answers = AnswerSet(
                decision_id=decision_id,
                answers={str(k): str(v) for k, v in sorted(answers_value.items(), key=lambda item: str(item[0]))},
            )
            draft = self._build_override.execute(intent=intent, answers=answers)
            ready_payload = {
                "decision_id": draft.decision_id,
                "context_id": draft.context_id,
                "policy_preset": draft.policy_preset,
                "truth_pack_alias": draft.truth_pack_alias,
                "user_override_ref": draft.user_override_ref,
                "override_hash": draft.override_hash,
                "explanation_text": draft.explanation_text,
            }
            self._ready_publisher.publish_ready(ready_payload)
        except Exception as exc:
            self._dlq_publisher.publish_dlq(
                reason_code="ANSWERS_INVALID",
                message=str(exc),
                payload={"source": "decision.truthpack.answers", "raw": dict(payload or {})},
            )


def _parse_intent(payload: Mapping[str, Any]) -> IntentEnvelope:
    decision_id = _required_text(payload, "decision_id")
    user_prompt = _required_text(payload, "user_prompt")
    context_id = _required_text(payload, "context_id") or "dev"
    policy_preset = _required_text(payload, "policy_preset") or "dev"
    if not decision_id:
        raise ValueError("decision_id is required")
    if not user_prompt:
        raise ValueError("user_prompt is required")
    return IntentEnvelope(
        decision_id=decision_id,
        context_id=context_id,
        policy_preset=policy_preset,
        user_prompt=user_prompt,
    )


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    return str(value).strip() if isinstance(value, str) else ""
