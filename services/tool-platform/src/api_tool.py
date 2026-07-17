from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from babyai.tools.result import ToolResult, duration_ms, ensure_audit_sink, log_tool_call


_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class ApiTool:
    def __init__(
        self,
        endpoint: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        payload: Any | None = None,
        auth_ref: str | None = None,
        timeout: float = 15.0,
        http_client: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        clean_endpoint = str(endpoint or "").strip()
        if not clean_endpoint:
            raise ValueError("endpoint must be non-empty")
        self.endpoint = clean_endpoint
        self.method = str(method or "GET").strip().upper()
        self.headers = dict(headers or {})
        self.payload = payload
        self.auth_ref = str(auth_ref or "").strip() or None
        self.timeout = max(0.01, float(timeout or 15.0))
        self._http_client = http_client

    def permission_level(self) -> str:
        if self.method in _MUTATING_METHODS:
            return "high"
        if self.auth_ref:
            return "medium"
        return "low"

    def execute(
        self,
        *,
        project_id: str,
        domain: str,
        memory_ref: Any,
        agent_id: str | None = None,
        secrets_ref: Mapping[str, str] | None = None,
    ) -> ToolResult:
        sink = ensure_audit_sink(memory_ref, project_id=project_id, domain=domain)
        started = datetime.now(timezone.utc)
        permission = self.permission_level()
        request_headers = dict(self.headers)
        request_payload = {
            "endpoint": self.endpoint,
            "method": self.method,
            "headers": _redact_headers(request_headers),
            "has_payload": self.payload is not None,
            "auth_ref": self.auth_ref,
            "timeout": self.timeout,
        }

        try:
            token = _resolve_auth_token(auth_ref=self.auth_ref, secrets_ref=secrets_ref, memory_ref=memory_ref)
            if token:
                request_headers["Authorization"] = f"Bearer {token}"
            request_payload["headers"] = _redact_headers(request_headers)
            if callable(self._http_client):
                raw_response = self._http_client(
                    {
                        "endpoint": self.endpoint,
                        "method": self.method,
                        "headers": dict(request_headers),
                        "payload": self.payload,
                        "timeout": self.timeout,
                    }
                )
            else:
                raw_response = httpx.request(
                    method=self.method,
                    url=self.endpoint,
                    headers=request_headers,
                    json=self.payload,
                    timeout=self.timeout,
                )

            status_code, response_body = _normalize_response(raw_response)
            finished = datetime.now(timezone.utc)
            ok = 200 <= int(status_code) < 300
            result = ToolResult(
                tool_name="api_tool",
                tool_type="api",
                permission_level=permission,
                ok=ok,
                output={
                    "status_code": int(status_code),
                    "response": response_body,
                },
                error=None if ok else "http_error",
                started_at=started.isoformat().replace("+00:00", "Z"),
                finished_at=finished.isoformat().replace("+00:00", "Z"),
                duration_ms=duration_ms(started_at=started, finished_at=finished),
            )
        except Exception as exc:
            finished = datetime.now(timezone.utc)
            result = ToolResult(
                tool_name="api_tool",
                tool_type="api",
                permission_level=permission,
                ok=False,
                output={"status_code": None, "response": None},
                error=f"request_error:{exc}",
                started_at=started.isoformat().replace("+00:00", "Z"),
                finished_at=finished.isoformat().replace("+00:00", "Z"),
                duration_ms=duration_ms(started_at=started, finished_at=finished),
            )

        log_tool_call(
            sink=sink,
            project_id=project_id,
            domain=domain,
            tool_name="api_tool",
            tool_type="api",
            permission_level=permission,
            request=request_payload,
            result=result,
            agent_id=agent_id,
        )
        return result


def _resolve_auth_token(*, auth_ref: str | None, secrets_ref: Mapping[str, str] | None, memory_ref: Any) -> str | None:
    clean_ref = str(auth_ref or "").strip()
    if not clean_ref:
        return None
    if isinstance(secrets_ref, Mapping) and clean_ref in secrets_ref:
        return str(secrets_ref[clean_ref])

    for method_name in ("resolve_secret", "get_secret"):
        method = getattr(memory_ref, method_name, None)
        if callable(method):
            token = method(clean_ref)
            if token is not None and str(token).strip():
                return str(token).strip()

    raise ValueError(f"auth_ref could not be resolved: {clean_ref}")


def _normalize_response(response: Any) -> tuple[int, Any]:
    if isinstance(response, httpx.Response):
        return int(response.status_code), _decode_httpx_body(response)
    if isinstance(response, tuple) and len(response) == 2:
        return int(response[0]), response[1]
    if isinstance(response, dict):
        status = int(response.get("status_code", 200))
        body = response.get("body", response.get("response"))
        return status, body
    raise ValueError("unsupported response payload from api client")


def _decode_httpx_body(response: httpx.Response) -> Any:
    content_type = str(response.headers.get("content-type", "")).lower()
    if "application/json" in content_type:
        try:
            return response.json()
        except Exception:
            return {"raw": response.text}
    return response.text


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in headers.items():
        if str(key).lower() == "authorization":
            out[str(key)] = "***"
        else:
            out[str(key)] = str(value)
    return out
