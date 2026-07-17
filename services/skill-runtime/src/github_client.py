"""
babyai/skills/github_client.py — Thin async GitHub API client for skill discovery.

Uses aiohttp (already a declared dependency in pyproject.toml).

Public API:
    client = GitHubClient()
    repos  = await client.search_skill_repos("trading")
    md     = await client.fetch_skill_md("owner/repo")
"""
from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

_log = logging.getLogger(__name__)

_SEARCH_URL  = "https://api.github.com/search/repositories"
_RAW_BASE    = "https://raw.githubusercontent.com"
_SKILL_FILE  = "SKILL.md"
_TIMEOUT_S   = 10

_BASE_HEADERS: dict[str, str] = {
    "Accept":             "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _headers() -> dict[str, str]:
    h = dict(_BASE_HEADERS)
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


class GitHubClient:
    """Thin wrapper around the GitHub REST API for skill repo discovery."""

    async def search_skill_repos(
        self, query: str, max_results: int = 10
    ) -> list[dict[str, Any]]:
        """
        Search GitHub for repositories that contain SKILL.md and match query.

        Returns list of repository dicts from GitHub API (keys: full_name,
        stargazers_count, description, html_url, …).
        Returns [] on rate-limit (403/429) or any network/timeout error.
        """
        params = {
            "q":        f"SKILL.md in:path {query}",
            "sort":     "stars",
            "per_page": str(max_results),
        }
        timeout = aiohttp.ClientTimeout(total=_TIMEOUT_S)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    _SEARCH_URL, params=params, headers=_headers()
                ) as resp:
                    if resp.status in (403, 429):
                        _log.warning(
                            "github_rate_limit status=%s query=%r — returning []",
                            resp.status, query,
                        )
                        return []
                    if resp.status != 200:
                        _log.warning(
                            "github_search_error status=%s query=%r — returning []",
                            resp.status, query,
                        )
                        return []
                    data = await resp.json(content_type=None)
            return list(data.get("items", []))
        except Exception as exc:
            _log.warning("github_search_failed query=%r error=%s", query, exc)
            return []

    async def fetch_skill_md(self, repo_full_name: str) -> str | None:
        """
        Fetch SKILL.md from repo_full_name (e.g. "owner/repo").

        Tries /main/SKILL.md first, falls back to /master/SKILL.md.
        Returns None on 404 or any error.
        """
        timeout = aiohttp.ClientTimeout(total=_TIMEOUT_S)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                for branch in ("main", "master"):
                    url = f"{_RAW_BASE}/{repo_full_name}/{branch}/{_SKILL_FILE}"
                    try:
                        async with session.get(url, headers=_headers()) as resp:
                            if resp.status == 200:
                                return await resp.text()
                    except Exception:
                        continue
        except Exception as exc:
            _log.warning(
                "fetch_skill_md_failed repo=%r error=%s", repo_full_name, exc
            )
        return None
