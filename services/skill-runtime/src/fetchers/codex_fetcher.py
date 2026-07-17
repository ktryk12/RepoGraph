from __future__ import annotations

from typing import Optional, Tuple

import aiohttp


class CodexSkillFetcher:
    TIMEOUT_SECONDS = 5
    _RAW_HOST = "https://raw.githubusercontent.com"

    async def fetch(self, uri: str) -> str:
        parsed = self._parse_uri(uri)
        if parsed is None:
            return ""
        owner, repo, path = parsed
        url = f"{self._RAW_HOST}/{owner}/{repo}/HEAD/{path}"
        timeout = aiohttp.ClientTimeout(total=float(self.TIMEOUT_SECONDS))
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

    def _parse_uri(self, uri: str) -> Optional[Tuple[str, str, str]]:
        normalized = str(uri or "").strip().strip("/")
        parts = [part for part in normalized.split("/") if part]
        if len(parts) < 3:
            return None
        return parts[0], parts[1], "/".join(parts[2:])

