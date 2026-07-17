"""
tools/firecrawl_client.py — Web intelligence client for BabyAI.

Two backends — first match wins:

1. Self-hosted (local-first, no data leaves your network):
   Set FIRECRAWL_API_URL=http://firecrawl:3002 (Docker)
             or          http://localhost:3002  (local)
   No API key needed. Uses httpx directly.

2. Cloud SDK fallback:
   Set FIRECRAWL_API_KEY=fc-...
   Requires: pip install firecrawl-py

3. Stub mode — returns empty silently when neither is configured.

All methods return empty dict/list on ANY error — never raise.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

_log = logging.getLogger(__name__)

# Central bank URL registry
_CENTRAL_BANK_URLS: Dict[str, str] = {
    "fed":            "https://www.federalreserve.gov/newsevents/pressreleases.htm",
    "ecb":            "https://www.ecb.europa.eu/press/pr/date/",
    "nationalbanken": "https://www.nationalbanken.dk/da/presse/pressemeddelelser",
}

_warned_no_backend = False


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def _self_hosted_url() -> str | None:
    """Return self-hosted base URL if FIRECRAWL_API_URL is set, else None."""
    url = os.getenv("FIRECRAWL_API_URL", "").rstrip("/")
    return url if url else None


def _get_sdk_app() -> Any:
    """Return FirecrawlApp if FIRECRAWL_API_KEY is set, else None."""
    api_key = os.getenv("FIRECRAWL_API_KEY", "")
    if not api_key:
        return None
    try:
        from firecrawl import FirecrawlApp  # noqa: PLC0415
        return FirecrawlApp(api_key=api_key)
    except Exception as exc:
        _log.warning("firecrawl_sdk_init_failed error=%s", exc)
        return None


def _warn_no_backend() -> None:
    global _warned_no_backend
    if not _warned_no_backend:
        _log.warning(
            "FirecrawlClient: no backend configured — running in stub mode. "
            "Self-host: set FIRECRAWL_API_URL=http://localhost:3002  "
            "or cloud: set FIRECRAWL_API_KEY=fc-<key>"
        )
        _warned_no_backend = True


# ---------------------------------------------------------------------------
# httpx helpers for self-hosted backend
# ---------------------------------------------------------------------------

def _httpx_scrape(base_url: str, url: str) -> Dict[str, Any]:
    """POST /v1/scrape to self-hosted Firecrawl. Returns normalised dict."""
    try:
        import httpx
        resp = httpx.post(
            f"{base_url}/v1/scrape",
            json={"url": url, "formats": ["markdown"]},
            timeout=30.0,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            _log.warning("firecrawl_scrape_not_success url=%s body=%s", url, body)
            return {}
        data = body.get("data", {})
        return {
            "content": data.get("markdown", data.get("content", "")),
            "title":   (data.get("metadata") or {}).get("title", ""),
            "url":     (data.get("metadata") or {}).get("url", url),
        }
    except Exception as exc:
        _log.warning("firecrawl_httpx_scrape_failed url=%s error=%s", url, exc)
        return {}


def _httpx_search(base_url: str, query: str, limit: int) -> List[Dict[str, Any]]:
    """POST /v1/search to self-hosted Firecrawl. Returns list of normalised dicts."""
    try:
        import httpx
        resp = httpx.post(
            f"{base_url}/v1/search",
            json={"query": query, "limit": limit, "scrapeOptions": {"formats": ["markdown"]}},
            timeout=45.0,
        )
        resp.raise_for_status()
        body = resp.json()
        if not body.get("success"):
            _log.warning("firecrawl_search_not_success query=%r body=%s", query, body)
            return []
        items = body.get("data", [])
        output = []
        for item in items[:limit]:
            output.append({
                "content": item.get("markdown", item.get("content", "")),
                "title":   (item.get("metadata") or {}).get("title", item.get("title", "")),
                "url":     (item.get("metadata") or {}).get("url", item.get("url", "")),
            })
        return output
    except Exception as exc:
        _log.warning("firecrawl_httpx_search_failed query=%r error=%s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Public client
# ---------------------------------------------------------------------------

class FirecrawlClient:
    """
    Web intelligence client for BabyAI agents.

    Backend priority:
      1. Self-hosted  — FIRECRAWL_API_URL (httpx, no key, local-first)
      2. Cloud SDK    — FIRECRAWL_API_KEY (firecrawl-py)
      3. Stub         — returns empty, logs warning once

    All methods return dict or list — never raw objects, never raise.
    """

    def scrape_page(self, url: str) -> Dict[str, Any]:
        """
        Scrape a single page and return clean markdown + metadata.

        Returns {"content": str, "title": str, "url": str}
        or empty dict on any error / no backend.
        """
        base = _self_hosted_url()
        if base:
            return _httpx_scrape(base, url)

        app = _get_sdk_app()
        if app:
            try:
                result = app.scrape_url(url, params={"formats": ["markdown"]})
                if not result:
                    return {}
                return {
                    "content": result.get("markdown", result.get("content", "")),
                    "title":   result.get("metadata", {}).get("title", ""),
                    "url":     result.get("metadata", {}).get("url", url),
                }
            except Exception as exc:
                _log.warning("firecrawl_sdk_scrape_failed url=%s error=%s", url, exc)
                return {}

        _warn_no_backend()
        return {}

    def scrape_central_bank_statement(self, source: str) -> Dict[str, Any]:
        """
        Scrape latest press release page from a central bank.

        source: "fed" | "ecb" | "nationalbanken"
        Returns {"content", "title", "url", "source"} or empty dict.
        """
        url = _CENTRAL_BANK_URLS.get(source.lower())
        if not url:
            _log.warning("firecrawl_unknown_central_bank source=%s", source)
            return {}
        result = self.scrape_page(url)
        if result:
            result["source"] = source
        return result

    def scrape_crypto_project(self, url: str) -> Dict[str, Any]:
        """
        Scrape a crypto project page or whitepaper URL.

        Returns {"content": str, "title": str, "url": str} or empty dict.
        """
        return self.scrape_page(url)

    def search_and_scrape(
        self,
        query: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Web search + scrape top results for a query.

        Returns list of {"content", "title", "url"} dicts.
        Returns empty list if no backend or on error.
        """
        base = _self_hosted_url()
        if base:
            return _httpx_search(base, query, limit)

        app = _get_sdk_app()
        if app:
            try:
                results = app.search(
                    query,
                    params={"limit": limit, "scrapeOptions": {"formats": ["markdown"]}},
                )
                if not results:
                    return []
                items = results if isinstance(results, list) else results.get("data", [])
                output = []
                for item in items[:limit]:
                    output.append({
                        "content": item.get("markdown", item.get("content", "")),
                        "title":   item.get("metadata", {}).get("title", item.get("title", "")),
                        "url":     item.get("metadata", {}).get("url", item.get("url", "")),
                    })
                return output
            except Exception as exc:
                _log.warning("firecrawl_sdk_search_failed query=%r error=%s", query, exc)
                return []

        _warn_no_backend()
        return []
