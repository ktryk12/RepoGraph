from __future__ import annotations

import json
from urllib.request import Request, urlopen

from application.ports import PolicyValidatorPort, PolicyValidatorResult
from domain.models import DecisionRequest
from domain.services import canonicalize_request


class HttpPolicyValidatorAdapter(PolicyValidatorPort):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 3.0,
    ) -> None:
        self._base_url = str(base_url).rstrip("/")
        self._api_key = str(api_key or "").strip()
        self._timeout_seconds = float(timeout_seconds)

    def validate_request(self, request: DecisionRequest) -> PolicyValidatorResult:
        payload = {
            "action": "constitution_unchanged",
            "context": canonicalize_request(request),
        }
        body = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
        }
        if self._api_key:
            headers["x-api-key"] = self._api_key
        req = Request(
            url=f"{self._base_url}/v1/policy/require",
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with urlopen(req, timeout=self._timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            return PolicyValidatorResult(
                allowed=False,
                reason_code="POLICY_VALIDATOR_UNAVAILABLE",
                message=str(exc),
                metadata={},
            )

        try:
            parsed = json.loads(raw or "{}")
        except Exception:
            return PolicyValidatorResult(
                allowed=False,
                reason_code="POLICY_VALIDATOR_INVALID_RESPONSE",
                message="response was not valid JSON",
                metadata={"raw": raw},
            )
        if not isinstance(parsed, dict):
            return PolicyValidatorResult(
                allowed=False,
                reason_code="POLICY_VALIDATOR_INVALID_RESPONSE",
                message="response was not an object",
                metadata={"raw": parsed},
            )

        allowed = bool(parsed.get("allowed"))
        if allowed:
            return PolicyValidatorResult(allowed=True, metadata=parsed)
        return PolicyValidatorResult(
            allowed=False,
            reason_code=str(parsed.get("rule_id") or parsed.get("reason_code") or "POLICY_VALIDATOR_DENIED"),
            message=str(parsed.get("message") or "policy validator denied enqueue_decision"),
            metadata=parsed,
        )
