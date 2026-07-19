"""One token budget engine shared by retrieval, compression and packing."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from .profiles import TokenizerProfile, resolve_profile

TokenCounter = Callable[[str], int]
Payload = str | bytes | Mapping[str, Any] | Sequence[Any] | None

_LEXEMES = re.compile(
    r"\s+|[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[^\w\s]",
    re.UNICODE,
)
_IDENTIFIER_PARTS = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|\d+")


@dataclass(frozen=True, slots=True)
class BudgetRequest:
    total_context: int
    target_model: str | None = None
    system_instructions: Payload = None
    required_tool_schemas: Payload = None
    active_task_memory: Payload = None
    code_and_documentation: Payload = None
    tool_results: Payload = None
    reserved_output_tokens: int = 0
    safety_margin_tokens: int = 0
    safety_margin_ratio: float = 0.0


@dataclass(frozen=True, slots=True)
class TokenBudget:
    total_context: int
    profile: str
    tokenizer: str
    exact: bool
    component_tokens: dict[str, int] = field(default_factory=dict)
    reserved_output_tokens: int = 0
    safety_margin_tokens: int = 0
    available_retrieval_tokens: int = 0

    @property
    def used_non_retrieval_tokens(self) -> int:
        return sum(self.component_tokens.values()) + self.reserved_output_tokens + self.safety_margin_tokens


class TokenBudgetEngine:
    """Model-aware token counter and context-window allocator.

    RepoGraph never invokes a model.  An exact tokenizer is used only when it is
    available locally or explicitly registered by the embedding application.
    """

    _registered_counters: dict[str, TokenCounter] = {}

    def __init__(self, target_model: str | None = None) -> None:
        self.target_model = target_model
        self.profile: TokenizerProfile = resolve_profile(target_model)
        self._counter, self.exact = self._resolve_counter()

    @classmethod
    def register_counter(cls, profile: str, counter: TokenCounter) -> None:
        cls._registered_counters[profile.lower()] = counter
        get_engine.cache_clear()

    @classmethod
    def unregister_counter(cls, profile: str) -> None:
        cls._registered_counters.pop(profile.lower(), None)
        get_engine.cache_clear()

    def count_text(self, text: str | bytes | None) -> int:
        if text is None or text == "" or text == b"":
            return 0
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        return max(1, int(self._counter(text)))

    def count_payload(self, payload: Payload) -> int:
        if payload is None:
            return 0
        if isinstance(payload, (str, bytes)):
            return self.count_text(payload)
        if not payload:
            return 0
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return self.count_text(serialized)

    def calculate(self, request: BudgetRequest) -> TokenBudget:
        total = max(0, int(request.total_context))
        components = {
            "system_instructions": self.count_payload(request.system_instructions),
            "required_tool_schemas": self.count_payload(request.required_tool_schemas),
            "active_task_memory": self.count_payload(request.active_task_memory),
            "code_and_documentation": self.count_payload(request.code_and_documentation),
            "tool_results": self.count_payload(request.tool_results),
        }
        reserved_output = max(0, int(request.reserved_output_tokens))
        ratio_margin = math.ceil(total * max(0.0, request.safety_margin_ratio))
        safety_margin = max(max(0, int(request.safety_margin_tokens)), ratio_margin)
        unavailable = sum(components.values()) + reserved_output + safety_margin
        return TokenBudget(
            total_context=total,
            profile=self.profile.name,
            tokenizer=self.profile.tokenizer,
            exact=self.exact,
            component_tokens=components,
            reserved_output_tokens=reserved_output,
            safety_margin_tokens=safety_margin,
            available_retrieval_tokens=max(0, total - unavailable),
        )

    def truncate_text(self, text: str, max_tokens: int) -> str:
        """Return the longest prefix that fits according to this engine."""
        if max_tokens <= 0 or not text:
            return ""
        if self.count_text(text) <= max_tokens:
            return text
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self.count_text(text[:middle]) <= max_tokens:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip()

    def _resolve_counter(self) -> tuple[TokenCounter, bool]:
        registered = self._registered_counters.get(self.profile.name)
        if registered is not None:
            return registered, True
        if self.profile.name == "openai":
            counter = self._optional_tiktoken_counter()
            if counter is not None:
                return counter, True
        return self._lexical_count, False

    def _optional_tiktoken_counter(self) -> TokenCounter | None:
        try:
            import tiktoken  # type: ignore[import-not-found]

            try:
                encoding = tiktoken.encoding_for_model(self.target_model or "gpt-4o")
            except KeyError:
                encoding = tiktoken.get_encoding("cl100k_base")
            return lambda text: len(encoding.encode(text))
        except (ImportError, ModuleNotFoundError):
            return None

    def _lexical_count(self, text: str) -> int:
        """Code-aware fallback that accounts for identifiers and punctuation."""
        total = 0
        ratio = self.profile.fallback_chars_per_token
        for lexeme in _LEXEMES.findall(text):
            if lexeme.isspace():
                total += max(0, math.ceil(len(lexeme) / 8))
            elif lexeme[0].isalpha() or lexeme[0] == "_":
                parts = _IDENTIFIER_PARTS.findall(lexeme.replace("_", " ")) or [lexeme]
                total += sum(max(1, math.ceil(len(part) / ratio)) for part in parts)
            elif lexeme[0].isdigit():
                total += max(1, math.ceil(len(lexeme) / 3))
            else:
                total += 1
        return max(1, total)


@lru_cache(maxsize=128)
def get_engine(target_model: str | None = None) -> TokenBudgetEngine:
    return TokenBudgetEngine(target_model)


def count_tokens(text: str | bytes | None, target_model: str | None = None) -> int:
    return get_engine(target_model).count_text(text)
