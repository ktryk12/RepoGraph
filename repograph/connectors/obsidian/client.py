"""HTTP client for communicating with Obsidian Local REST API."""

import os
from typing import Any
import httpx

from .exceptions import (
    ObsidianConfigurationError,
    ObsidianTimeoutError,
    ObsidianUnauthorizedError,
    ObsidianConnectorError,
)

class ObsidianClient:
    def __init__(self):
        self.uri = os.getenv("OBSIDIAN_REST_API_URI", "").rstrip("/")
        self.api_key = os.getenv("OBSIDIAN_API_KEY", "")
        
        self.configured = bool(self.uri and self.api_key)
        
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        # By default Local REST API exposes self-signed certificates.
        # We disable strict SSL verification here for seamless local developer testing.
        self.client = httpx.Client(
            headers=self.headers,
            verify=False,
            timeout=httpx.Timeout(5.0, connect=2.0)
        )

    def healthcheck(self) -> dict[str, Any]:
        """Pings the root endpoint to check connectivity and auth."""
        if not self.configured:
            raise ObsidianConfigurationError("OBSIDIAN_REST_API_URI and OBSIDIAN_API_KEY not set.")
            
        try:
            resp = self.client.get(f"{self.uri}/")
            self._check_response(resp)
            return {"status": "ok", "configured": True}
        except httpx.TimeoutException as exc:
            raise ObsidianTimeoutError(f"Timeout connecting to Obsidian: {exc}") from exc
        except httpx.RequestError as exc:
            raise ObsidianConnectorError(f"Request failed: {exc}") from exc

    def search_simple(self, query: str) -> list[dict[str, Any]]:
        """Performs a simple full-text search against Obsidian."""
        if not self.configured:
            return []
            
        try:
            resp = self.client.post(f"{self.uri}/search/simple/", params={"query": query})
            self._check_response(resp)
            data = resp.json()
            return data if isinstance(data, list) else []
        except httpx.TimeoutException as exc:
            raise ObsidianTimeoutError(str(exc)) from exc
        except httpx.RequestError as exc:
            raise ObsidianConnectorError(str(exc)) from exc

    def search(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Performs a structured JSON search (e.g. JsonLogic or Dataview query)."""
        if not self.configured:
            return []
            
        try:
            resp = self.client.post(f"{self.uri}/search/", json=payload)
            self._check_response(resp)
            data = resp.json()
            return data if isinstance(data, list) else []
        except httpx.TimeoutException as exc:
            raise ObsidianTimeoutError(str(exc)) from exc
        except httpx.RequestError as exc:
            raise ObsidianConnectorError(str(exc)) from exc

    def _check_response(self, resp: httpx.Response) -> None:
        if resp.status_code in (401, 403):
            raise ObsidianUnauthorizedError(f"Unauthorized: {resp.status_code} - {resp.text}")
        resp.raise_for_status()

    def close(self):
        self.client.close()
