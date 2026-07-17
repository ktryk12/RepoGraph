from __future__ import annotations

import json
import logging
import re
import unicodedata
import urllib.parse
from typing import Any

logger = logging.getLogger(__name__)


class InjectionDetectedError(Exception):
    def __init__(self, source: str, pattern: str, snippet: str):
        self.source = source
        self.pattern = pattern
        self.snippet = snippet
        super().__init__(f"Injection detected in {source}: {pattern}")


class InjectionScanner:
    INJECTION_PATTERNS = [
        r"\bignore\s+previous\s+instructions?\b",
        r"\bdisregard\s+(?:the\s+)?system\s+prompt\b",
        r"\[(?:system|assistant)\]",
        r"<\s*system\s*>",
        r"\bscore_a\s*[:=]\s*1(?:\.0+)?\b",
        r"\bwinning_policy\s*[:=]",
        r"\bact\s+as\b",
        r"\bpretend\b",
    ]

    _HOMOGLYPH_MAP = str.maketrans(
        {
            "\u0456": "i",  # Cyrillic small i
            "\u0406": "I",  # Cyrillic capital i
            "\u0131": "i",  # Latin dotless i
        }
    )

    def __init__(self, patterns: list[str] | None = None, redis_client: Any | None = None) -> None:
        self.INJECTION_PATTERNS = list(patterns or self.INJECTION_PATTERNS)
        self.redis = redis_client

    def add_pattern(self, pattern: str) -> None:
        self.INJECTION_PATTERNS.append(str(pattern))

    def scan(self, text: str, source: str) -> None:
        normalized = self._normalize(text)
        for pattern in self.INJECTION_PATTERNS:
            match = re.search(pattern, normalized, flags=re.IGNORECASE)
            if match is None:
                continue
            snippet = _snippet(normalized, match.start(), match.end())
            logger.warning(
                "security_event event_type=security.injection_detected source=%s pattern=%s snippet=%s",
                source,
                pattern,
                snippet,
            )
            raise InjectionDetectedError(source=source, pattern=pattern, snippet=snippet)

    def _normalize(self, text: str) -> str:
        decoded = urllib.parse.unquote(str(text or ""))
        nfkc = unicodedata.normalize("NFKC", decoded)
        homogenized = nfkc.translate(self._HOMOGLYPH_MAP)
        compact = re.sub(r"\s+", " ", homogenized).strip()
        return compact

    async def start_policy_listener(self) -> None:
        if self.redis is None:
            return
        pubsub_factory = getattr(self.redis, "pubsub", None)
        if not callable(pubsub_factory):
            return
        pubsub = pubsub_factory()
        if hasattr(pubsub, "__aenter__"):
            async with pubsub as ps:
                await self._listen_policy_updates(ps)
            return
        await self._listen_policy_updates(pubsub)

    async def _listen_policy_updates(self, pubsub: Any) -> None:
        subscribe = getattr(pubsub, "subscribe", None)
        if callable(subscribe):
            result = subscribe("babyai:policy_updates")
            if hasattr(result, "__await__"):
                await result
        listen = getattr(pubsub, "listen", None)
        if not callable(listen):
            return
        async for message in listen():
            action = _decode_action_message(message)
            if not isinstance(action, dict):
                continue
            if str(action.get("type") or "").strip().lower() != "normalization":
                continue
            new_pattern = str(action.get("new_pattern") or "").strip()
            if not new_pattern:
                continue
            self.add_pattern(new_pattern)
            logger.info(
                "security_event event_type=security.pattern_hot_reloaded pattern=%s",
                new_pattern,
            )


def _snippet(text: str, start: int, end: int, *, span: int = 50) -> str:
    left = max(0, int(start) - int(span))
    right = min(len(text), int(end) + int(span))
    return text[left:right]


def _decode_action_message(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    raw = message.get("data")
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    if not isinstance(raw, str):
        return None
    try:
        decoded = json.loads(raw)
    except Exception:
        return None
    if not isinstance(decoded, dict):
        return None
    return decoded
