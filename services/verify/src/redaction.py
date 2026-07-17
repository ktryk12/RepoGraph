from __future__ import annotations

import re
from typing import Iterable, List


DEFAULT_PATTERNS = [
    r"AKIA[0-9A-Z]{16}",
    r"ASIA[0-9A-Z]{16}",
    r"FAKE_AWS_KEY=[A-Z0-9]{16,}",
    r"sk_live_[0-9a-zA-Z]{16,}",
    r"sk_test_[0-9a-zA-Z]{16,}",
]


def redact_text(text: str, patterns: Iterable[str] = DEFAULT_PATTERNS) -> str:
    redacted = text
    for pat in patterns:
        redacted = re.sub(pat, "[REDACTED]", redacted)
    return redacted


def contains_secret(text: str, patterns: Iterable[str] = DEFAULT_PATTERNS) -> bool:
    for pat in patterns:
        if re.search(pat, text):
            return True
    return False
