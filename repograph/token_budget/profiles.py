"""Tokenizer profile resolution without introducing an LLM dependency.

Profiles describe tokenization families.  Exact counters can be registered by a
host (or are picked up from optional local tokenizer packages); the built-in
lexical estimator remains deterministic and dependency-free.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TokenizerProfile:
    name: str
    tokenizer: str
    fallback_chars_per_token: float
    aliases: tuple[str, ...]


PROFILES: tuple[TokenizerProfile, ...] = (
    TokenizerProfile(
        name="openai",
        tokenizer="tiktoken",
        fallback_chars_per_token=3.8,
        aliases=("openai", "gpt", "codex", "o1", "o3", "o4"),
    ),
    TokenizerProfile(
        name="anthropic",
        tokenizer="anthropic",
        fallback_chars_per_token=3.6,
        aliases=("anthropic", "claude"),
    ),
    TokenizerProfile(
        name="gemini",
        tokenizer="sentencepiece",
        fallback_chars_per_token=4.0,
        aliases=("google", "gemini", "gemma"),
    ),
    TokenizerProfile(
        name="local",
        tokenizer="huggingface",
        fallback_chars_per_token=3.4,
        aliases=(
            "local", "qwen", "llama", "mistral", "mixtral", "deepseek",
            "glm", "ollama", "vllm", "newmodel", "hashlogmoe",
        ),
    ),
    TokenizerProfile(
        name="generic",
        tokenizer="lexical",
        fallback_chars_per_token=4.0,
        aliases=("generic",),
    ),
)


def resolve_profile(model: str | None = None) -> TokenizerProfile:
    """Resolve a model identifier to a stable tokenizer family."""
    normalized = (model or "generic").strip().lower()
    for profile in PROFILES:
        if normalized == profile.name or any(alias in normalized for alias in profile.aliases):
            return profile
    return PROFILES[-1]
