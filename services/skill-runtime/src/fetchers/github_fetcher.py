from __future__ import annotations

from typing import Optional, Tuple

import aiohttp


class GitHubSkillFetcher:
    TIMEOUT_SECONDS = 5
    _RAW_HOST = "https://raw.githubusercontent.com"

    async def fetch(self, uri: str) -> str:
        parsed = self._parse_uri(uri)
        if parsed is None:
            return ""
        owner, repo, path = parsed
        if not path:
            url = f"{self._RAW_HOST}/{owner}/{repo}/HEAD/README.md"
            return await self._read_text(url, timeout_seconds=self.TIMEOUT_SECONDS)
        url = f"{self._RAW_HOST}/{owner}/{repo}/HEAD/{path}"
        return await self._read_text(url, timeout_seconds=self.TIMEOUT_SECONDS)

    def _parse_uri(self, uri: str) -> Optional[Tuple[str, str, str]]:
        normalized = str(uri or "").strip().strip("/")
        parts = [part for part in normalized.split("/") if part]
        if len(parts) < 2:
            return None
        owner = parts[0]
        repo = parts[1]
        path = "/".join(parts[2:])
        return owner, repo, path

    async def _read_text(self, url: str, *, timeout_seconds: int) -> str:
        timeout = aiohttp.ClientTimeout(total=float(timeout_seconds))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if int(response.status) == 404:
                        return ""
                    if int(response.status) >= 400:
                        return ""
                    return await response.text()
        except Exception:
            return ""

