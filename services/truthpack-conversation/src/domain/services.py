from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import json
from typing import Any, Mapping

import yaml

from .models import AnswerSet, Question

# Must match truth/loader.py immutables.
IMMUTABLE_ROOT_KEYS: tuple[str, ...] = ("privacy", "redaction", "logging", "gates")


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(_canonicalize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def generate_questions(*, intent: str, policy_preset: str) -> list[Question]:
    safe_intent = _normalize_text(intent)
    safe_policy = _normalize_policy(policy_preset)
    prompts = (
        ("goal", "What concrete outcome should BabyAI deliver?"),
        ("acceptance", "How should we verify that the outcome is correct?"),
        ("constraints", "Any constraints or non-goals we must respect?"),
    )
    questions: list[Question] = []
    for key, prompt in prompts:
        qid = _stable_id(f"{safe_policy}:{key}:{prompt}")
        questions.append(Question(question_id=qid, prompt=prompt, required=True))
    # Intent is included in the seed only to keep determinism tied to request content.
    _ = sha256(safe_intent.encode("utf-8")).hexdigest()
    return questions


def build_override(
    *,
    decision_id: str,
    context_id: str,
    intent: str,
    answers: AnswerSet,
    policy_preset: str,
) -> tuple[dict[str, Any], str, str, str]:
    safe_policy = _normalize_policy(policy_preset)
    safe_intent = _normalize_text(intent)
    normalized_answers = {
        str(k): _normalize_text(v)
        for k, v in sorted((answers.answers or {}).items(), key=lambda item: str(item[0]))
    }

    override_payload = {
        "version": 1,
        "metadata": {
            "generated_by": "truthpack_conversation",
            "policy_preset": safe_policy,
        },
        "task": {
            "intent": safe_intent,
            "answers": normalized_answers,
        },
        "budgets": _preset_budget(safe_policy),
        "retries": _preset_retries(safe_policy),
    }
    assert_no_immutable_keys(override_payload)

    fingerprint_seed = {
        "intent": safe_intent,
        "answers": normalized_answers,
        "policy_preset": safe_policy,
    }
    override_hash = sha256(canonical_json(fingerprint_seed).encode("utf-8")).hexdigest()
    override_yaml = yaml.safe_dump(
        _canonicalize(override_payload),
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=False,
    )
    explanation_text = _build_explanation(
        intent=safe_intent,
        answers=normalized_answers,
        policy_preset=safe_policy,
        override_hash=override_hash,
    )
    _ = decision_id
    _ = context_id
    return override_payload, override_yaml, explanation_text, override_hash


def assert_no_immutable_keys(override_payload: Mapping[str, Any]) -> None:
    for key in IMMUTABLE_ROOT_KEYS:
        if key in override_payload:
            raise ValueError(f"immutable truth key override forbidden: {key}")


def question_to_payload(question: Question) -> dict[str, Any]:
    return asdict(question)


def _build_explanation(
    *,
    intent: str,
    answers: Mapping[str, str],
    policy_preset: str,
    override_hash: str,
) -> str:
    ordered_answers = "; ".join([f"{key}={value}" for key, value in sorted(answers.items())])
    return (
        f"policy={policy_preset}; intent={intent}; answers={ordered_answers}; "
        f"override_hash={override_hash}"
    )


def _preset_budget(policy_preset: str) -> dict[str, int]:
    if policy_preset == "restricted":
        return {"max_repairs": 1, "max_steps": 100}
    if policy_preset == "public":
        return {"max_repairs": 2, "max_steps": 150}
    return {"max_repairs": 3, "max_steps": 200}


def _preset_retries(policy_preset: str) -> dict[str, int]:
    if policy_preset == "restricted":
        return {"max_attempts": 2, "backoff_seconds": 8}
    if policy_preset == "public":
        return {"max_attempts": 2, "backoff_seconds": 5}
    return {"max_attempts": 3, "backoff_seconds": 5}


def _normalize_policy(value: str) -> str:
    policy = str(value or "").strip().lower() or "dev"
    if policy not in {"public", "dev", "restricted"}:
        raise ValueError(f"unsupported policy_preset: {value}")
    return policy


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(seed: str) -> str:
    return f"q_{sha256(seed.encode('utf-8')).hexdigest()[:16]}"


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    return value
