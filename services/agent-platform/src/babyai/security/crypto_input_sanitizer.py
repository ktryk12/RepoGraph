"""
CryptoInputSanitizer — stateless input validation for crypto demo scripts.

Beskytter mod:
  1. Prompt injection (severity 0.98 → L7 auto-block)
  2. Data exfiltration forsøg (severity 0.97 → L7 auto-block)
  3. Malformet input / control characters (severity 0.85)
  4. For langt input (severity 0.6)

Brug:
    from babyai.security.crypto_input_sanitizer import CryptoInputSanitizer

    result = CryptoInputSanitizer.sanitize_question("Skal vi købe Bitcoin?")
    if not result["ok"]:
        CryptoInputSanitizer.log_violation(result, context="main()")
        if result["severity"] >= 0.95:
            sys.exit(1)

    result = CryptoInputSanitizer.sanitize_coin_ids(["bitcoin", "ethereum"])
    # result["clean"] = ["bitcoin", "ethereum"]
"""
from __future__ import annotations

import logging
import re
import unicodedata
import urllib.parse
from typing import Any

_log = logging.getLogger(__name__)

# ── Injection patterns ──────────────────────────────────────────────────────────
# Trigges med severity 0.98 (over L7 grænse på 0.95 → auto-block).
# Normaliseret tekst (NFKC + URL-decode) tjekkes mod disse.

_INJECTION_PATTERNS: list[str] = [
    # System / jailbreak overrides
    r"(?i)\bignore\s+(all\s+)?(previous\s+)?instruct",
    r"(?i)\byou\s+are\s+now\s+a\b",
    r"(?i)\bsystem\s*:\s*",
    r"(?i)\bforget\s+(everything|all)\b",
    r"(?i)\bnew\s+instruct",
    r"(?i)\bdisregard\s+(your|all)\b",
    r"(?i)\bpretend\s+(you\s+are|to\s+be)\b",
    r"(?i)\bact\s+as\s+(?:a\s+)?(?:new|different|unrestricted)\b",
    # Template / expression injection
    r"\{\{.{0,50}\}\}",   # {{expr}}
    r"\$\{.{0,50}\}",     # ${expr}
    r"<%[^>]{0,50}%>",    # <% %>
    # Shell / command injection
    r"[;&|`]",
    r"\.\.[/\\]",
    r"(?i)\b(exec|eval|system|popen)\s*\(",
    # Script injection
    r"(?i)<\s*script\b",
    r"(?i)\bjavascript\s*:",
    r"(?i)\bon\w+\s*=",
    # SQL injection
    r"(?i)(union\s+select|drop\s+table|insert\s+into)",
    r"(?i)'\s*(or|and)\s+'?\d",
    # Prompt / context leaking
    r"(?i)\b(show|print|dump|reveal)\b.{0,40}\bprompt\b",
    r"(?i)\bwhat\s+(are\s+)?your\s+instruct",
]

# ── Exfiltration patterns ───────────────────────────────────────────────────────
# Trigges med severity 0.97 (over L7 grænse → auto-block).

_EXFILTRATION_PATTERNS: list[str] = [
    r"(?i)/etc/(passwd|shadow|hosts)",
    r"(?i)\.env\b",
    r"(?i)\b(api[_\s]?keys?|secret[_\s]?key|password)\b",
    r"(?i)\bdump\b.{0,60}\b(memory|redis|kafka|config|secret|token)\b",
    r"(?i)\binternal\s+(config|data|state)\b",
    r"(?i)\b(reveal|show|read)\b.{0,40}\b(config|secret|key|token|credential)\b",
]

# ── Malformed input patterns ────────────────────────────────────────────────────
# Control characters (U+0000–U+0008, U+000B, U+000C, U+000E–U+001F, U+007F).
# Tjekkes FØR normalisering — null bytes kan ellers skjule injection-mønstre.

_MALFORMED_PATTERNS: list[str] = [
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",
]

# ── Regex for gyldige coin IDs ──────────────────────────────────────────────────
_COIN_ID_RE = re.compile(r"^[a-z0-9\-]+$")

# ── Coin injection patterns (shell, path traversal) ────────────────────────────
# Separate fra generelle injection patterns — tjekkes i sanitize_coin_ids.
_COIN_INJECTION_RE = re.compile(r"[;&|`$]|\.{2}[/\\]|<|>|'|\"|#")


class CryptoInputSanitizer:
    """
    Stateless input-validator til crypto demo.

    Alle metoder er @classmethod — ingen instantiering kræves.

    Return-format:
        {
            "ok":        bool,
            "clean":     str | list,   # renset input (tom ved fejl)
            "violation": str,          # tom streng hvis ok
            "severity":  float,        # 0.0 hvis ok
        }
    """

    MAX_QUESTION_LENGTH: int = 500
    MAX_COIN_ID_LENGTH: int = 50

    # ------------------------------------------------------------------
    # Offentlige metoder
    # ------------------------------------------------------------------

    @classmethod
    def sanitize_question(cls, question: str) -> dict[str, Any]:
        """
        Valider og rens et fritekst-spørgsmål.

        Tjekker i rækkefølge:
          1. Type-validering
          2. Malformed / control characters (severity 0.85)
          3. Længde (severity 0.6)
          4. Injection-mønstre (severity 0.98)
          5. Exfiltration-mønstre (severity 0.97)
        """
        if not isinstance(question, str):
            return cls._reject("Ugyldigt input type", 0.85)

        # 1. Malformed check — FØR normalisering
        for pat in _MALFORMED_PATTERNS:
            if re.search(pat, question):
                return cls._reject("Ugyldigt tegn i input", 0.85)

        # 2. Længde
        if len(question) > cls.MAX_QUESTION_LENGTH:
            return cls._reject(
                f"Spørgsmål for langt ({len(question)} tegn, max {cls.MAX_QUESTION_LENGTH})",
                0.6,
            )

        # 3. Injection (normaliseret tekst)
        normalized = cls._normalize(question)
        for pat in _INJECTION_PATTERNS:
            if re.search(pat, normalized):
                return cls._reject("Prompt injection pattern detekteret", 0.98)

        # 4. Exfiltration (normaliseret tekst)
        for pat in _EXFILTRATION_PATTERNS:
            if re.search(pat, normalized):
                return cls._reject("Data exfiltration forsøg detekteret", 0.97)

        return {
            "ok": True,
            "clean": cls._clean(question),
            "violation": "",
            "severity": 0.0,
        }

    @classmethod
    def sanitize_coin_ids(cls, coins: list[str]) -> dict[str, Any]:
        """
        Valider liste af coin IDs.

        Gyldige coin IDs: kun lowercase a-z, 0-9, bindestreg.
        Uppercase input normaliseres automatisk til lowercase.
        """
        if not coins:
            return {"ok": True, "clean": [], "violation": "", "severity": 0.0}

        clean_coins: list[str] = []
        for coin in coins:
            if not isinstance(coin, str):
                continue

            if len(coin) > cls.MAX_COIN_ID_LENGTH:
                return cls._reject(
                    f"Coin ID for langt: {coin[:20]}...",
                    0.6,
                )

            lowered = coin.lower()
            # Eksplicitte injection-tegn → højere severity (L7 trigger)
            if _COIN_INJECTION_RE.search(coin):
                return cls._reject(
                    f"Injection-tegn i coin ID: {coin[:20]}",
                    0.98,
                )
            if not _COIN_ID_RE.match(lowered):
                return cls._reject(
                    f"Ugyldigt coin ID format: {coin[:20]}",
                    0.85,
                )

            clean_coins.append(lowered)

        return {"ok": True, "clean": clean_coins, "violation": "", "severity": 0.0}

    @classmethod
    def log_violation(cls, result: dict[str, Any], context: str = "") -> None:
        """Log violation til warning log med fuld kontekst."""
        _log.warning(
            "[CryptoInputSanitizer] VIOLATION severity=%.2f reason=%r context=%r",
            result.get("severity", 0.0),
            result.get("violation", ""),
            context,
        )

    # ------------------------------------------------------------------
    # Interne metoder
    # ------------------------------------------------------------------

    @classmethod
    def _normalize(cls, text: str) -> str:
        """
        Normaliser tekst mod evasion:
          - URL-decode (%20 → space, %3B → ; osv.)
          - NFKC unicode normalisering (halvbredde → fuldbredde)
          - Compact whitespace
        """
        decoded = urllib.parse.unquote(str(text or ""))
        nfkc = unicodedata.normalize("NFKC", decoded)
        return re.sub(r"\s+", " ", nfkc).strip()

    @classmethod
    def _clean(cls, text: str) -> str:
        """Rens tekst for returner — fjern null bytes, normaliser unicode og whitespace."""
        text = text.replace("\x00", "")
        text = unicodedata.normalize("NFC", text)
        return " ".join(text.split())

    @classmethod
    def _reject(cls, reason: str, severity: float) -> dict[str, Any]:
        return {
            "ok": False,
            "clean": "",
            "violation": reason,
            "severity": severity,
        }
