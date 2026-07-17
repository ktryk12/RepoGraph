from __future__ import annotations

import aiohttp


class HuggingFaceSkillFetcher:
    TIMEOUT_SECONDS = 8
    MAX_CONTENT_CHARS = 6000

    async def fetch(self, uri: str) -> str:
        normalized = str(uri or "").strip().strip("/")
        if not normalized:
            return ""
        url = f"https://huggingface.co/{normalized}/raw/main/README.md"
        timeout = aiohttp.ClientTimeout(total=float(self.TIMEOUT_SECONDS))
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if int(response.status) == 404:
                        return ""
                    if int(response.status) >= 400:
                        return ""
                    text = await response.text()
        except Exception:
            return ""
        if len(text) > self.MAX_CONTENT_CHARS:
            return text[: self.MAX_CONTENT_CHARS]
        return text

