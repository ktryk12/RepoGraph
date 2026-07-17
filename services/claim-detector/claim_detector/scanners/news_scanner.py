"""
News scanner — bruger FirecrawlClient (genbrug fra tools/firecrawl_client.py).
Søger efter trending nyhedsartikler med potentielt faktatkekbare påstande.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from claim_detector.models import ClaimCandidate
from claim_detector.scanners.base import BaseScanner

_log = logging.getLogger("claim_detector.news_scanner")

_SEARCH_QUERIES = [
    "breaking news claim confirmed study shows",
    "fact check viral misleading false",
    "scientist says government admits leaked document",
]


class NewsScanner(BaseScanner):
    platform = "news"

    def __init__(self) -> None:
        self._firecrawl: Optional[Any] = self._load_firecrawl()

    def scan(self, *, limit: int = 50) -> List[ClaimCandidate]:
        if not self._firecrawl:
            _log.info("news_scanner_stub_no_firecrawl")
            return self._stub_candidates(limit)
        try:
            return self._scan_firecrawl(limit=limit)
        except Exception as exc:
            _log.warning("news_scanner_error error=%s", exc)
            return []

    def _scan_firecrawl(self, *, limit: int) -> List[ClaimCandidate]:
        candidates: List[ClaimCandidate] = []
        per_query = max(1, limit // len(_SEARCH_QUERIES))
        for query in _SEARCH_QUERIES:
            try:
                results = self._firecrawl.search_and_scrape(
                    query=query, max_results=per_query
                )
                for r in (results or []):
                    url     = r.get("url", "")
                    content = r.get("content", "") or r.get("markdown", "")
                    if not content or len(content) < 50:
                        continue
                    candidates.append(ClaimCandidate(
                        raw_text       = content[:500],
                        source_url     = url,
                        platform       = "news",
                        virality_score = 0.5,  # news defaults mid-virality
                    ))
            except Exception as exc:
                _log.debug("news_scanner_query_failed query=%s error=%s", query, exc)
        return candidates[:limit]

    @staticmethod
    def _stub_candidates(limit: int) -> List[ClaimCandidate]:
        return [
            ClaimCandidate(
                raw_text       = f"[STUB] News claim #{i+1} — configure FIRECRAWL_API_URL",
                source_url     = f"https://example.com/news/{i+1}",
                platform       = "news",
                virality_score = 0.3,
            )
            for i in range(min(3, limit))
        ]

    @staticmethod
    def _load_firecrawl() -> Optional[Any]:
        try:
            from tools.firecrawl_client import FirecrawlClient
            return FirecrawlClient()
        except Exception:
            return None
