from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict

from babyai.security.injection_scanner import InjectionScanner

logger = logging.getLogger(__name__)


class OutputValidationError(Exception):
    pass


class OutputValidator:
    ALLOWED_KEYS = frozenset({"score_a", "confidence", "rationale"})
    MAX_RATIONALE_LEN = 500

    def __init__(self, scanner: InjectionScanner | None = None) -> None:
        self.scanner = scanner

    def validate(self, raw: str, agent_id: str) -> Dict[str, Any]:
        clean = self._strip_fences(raw)
        payload = self._parse_json(clean, agent_id=agent_id)
        self._validate_keys(payload=payload, agent_id=agent_id)
        score_a = self._in_unit_interval(payload.get("score_a"), field="score_a", agent_id=agent_id)
        confidence = self._in_unit_interval(payload.get("confidence"), field="confidence", agent_id=agent_id)
        rationale = str(payload.get("rationale") or "")
        if len(rationale) > self.MAX_RATIONALE_LEN:
            self._raise_error(
                agent_id=agent_id,
                message=f"rationale_too_long len={len(rationale)} limit={self.MAX_RATIONALE_LEN}",
            )
        if self.scanner is not None and rationale.strip():
            self.scanner.scan(rationale, source=f"output_rationale:{agent_id}")
        return {
            "score_a": score_a,
            "confidence": confidence,
            "rationale": rationale,
        }

    @staticmethod
    def _strip_fences(raw: str) -> str:
        return re.sub(r"```(?:json)?|```", "", str(raw or "")).strip()

    def _parse_json(self, clean: str, *, agent_id: str) -> Dict[str, Any]:
        try:
            payload = json.loads(clean)
        except Exception as exc:
            self._raise_error(agent_id=agent_id, message=f"invalid_json: {exc}")
        if not isinstance(payload, dict):
            self._raise_error(agent_id=agent_id, message="payload_not_object")
        return dict(payload)

    def _validate_keys(self, *, payload: Dict[str, Any], agent_id: str) -> None:
        keys = set(payload.keys())
        missing = sorted(self.ALLOWED_KEYS - keys)
        extra = sorted(keys - self.ALLOWED_KEYS)
        if missing:
            self._raise_error(agent_id=agent_id, message=f"missing_keys: {missing}")
        if extra:
            self._raise_error(agent_id=agent_id, message=f"extra_keys: {extra}")

    def _in_unit_interval(self, value: Any, *, field: str, agent_id: str) -> float:
        try:
            parsed = float(value)
        except Exception:
            self._raise_error(agent_id=agent_id, message=f"{field}_not_numeric")
        if parsed < 0.0 or parsed > 1.0:
            self._raise_error(agent_id=agent_id, message=f"{field}_out_of_range")
        return float(parsed)

    def _raise_error(self, *, agent_id: str, message: str) -> None:
        logger.error(
            "security_event event_type=security.output_validation_failed agent_id=%s error=%s",
            agent_id,
            message,
        )
        raise OutputValidationError(message)
