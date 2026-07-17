from __future__ import annotations

import html
import logging
import re
import unicodedata

from babyai.security.injection_scanner import InjectionScanner

logger = logging.getLogger(__name__)


class RationaleGuard:
    MAX_RATIONALE_LEN = 500

    def __init__(self, scanner: InjectionScanner | None = None) -> None:
        self.scanner = scanner or InjectionScanner()

    def guard(self, rationale: str, agent_id: str) -> str:
        self.scanner.scan(str(rationale or ""), source=f"rationale:{agent_id}")
        sanitized = self._sanitize(str(rationale or ""))
        logger.info(
            "security_event event_type=rationale_flagged layer=4 agent_id=%s sanitized_len=%s",
            str(agent_id),
            len(sanitized),
        )
        return sanitized

    def _sanitize(self, text: str) -> str:
        stripped = re.sub(r"<[^>]+>", "", str(text or ""))
        escaped = html.escape(stripped)
        cleaned = "".join(
            ch for ch in escaped if unicodedata.category(ch)[0] != "C" or ch == "\n"
        )
        return cleaned[: self.MAX_RATIONALE_LEN].strip()
