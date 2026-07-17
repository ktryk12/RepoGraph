"""
YouTube Data API v3 scanner.
Requires: YOUTUBE_API_KEY
Falls back to stub if not configured.
"""
from __future__ import annotations

import logging
import os
from typing import List

from claim_detector.models import ClaimCandidate
from claim_detector.scanners.base import BaseScanner

_log = logging.getLogger("claim_detector.youtube_scanner")

_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
_SEARCH_TERMS = "fact check exposed truth revealed breaking"


class YouTubeScanner(BaseScanner):
    platform = "youtube"

    def scan(self, *, limit: int = 50) -> List[ClaimCandidate]:
        if not _API_KEY:
            _log.info("youtube_scanner_stub_no_key")
            return self._stub_candidates(limit)
        try:
            return self._scan_api(limit=limit)
        except Exception as exc:
            _log.warning("youtube_scanner_error error=%s", exc)
            return []

    def _scan_api(self, *, limit: int) -> List[ClaimCandidate]:
        import requests
        resp = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part":           "snippet",
                "q":              _SEARCH_TERMS,
                "type":           "video",
                "order":          "viewCount",
                "publishedAfter": _yesterday_iso(),
                "maxResults":     min(limit, 50),
                "key":            _API_KEY,
            },
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])

        candidates = []
        for item in items:
            snippet = item.get("snippet", {})
            vid_id  = item.get("id", {}).get("videoId", "")
            title   = snippet.get("title", "")
            desc    = snippet.get("description", "")
            text    = f"{title}. {desc}"[:500]
            candidates.append(ClaimCandidate(
                raw_text       = text,
                source_url     = f"https://www.youtube.com/watch?v={vid_id}",
                platform       = "youtube",
                virality_score = 0.4,  # no engagement data at search stage
            ))
        return candidates

    @staticmethod
    def _stub_candidates(limit: int) -> List[ClaimCandidate]:
        return [
            ClaimCandidate(
                raw_text       = f"[STUB] YouTube claim #{i+1} — configure YOUTUBE_API_KEY",
                source_url     = f"https://youtube.com/watch?v=stub{i+1}",
                platform       = "youtube",
                virality_score = 0.2,
            )
            for i in range(min(3, limit))
        ]


def _yesterday_iso() -> str:
    from datetime import date, timedelta, timezone
    from datetime import datetime
    d = date.today() - timedelta(days=1)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")
