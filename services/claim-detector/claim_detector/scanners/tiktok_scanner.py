"""
TikTok Research API scanner.
Requires: TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET (OAuth2 app creds)
Falls back to stub if not configured.
"""
from __future__ import annotations

import logging
import os
import time
from typing import List

from claim_detector.models import ClaimCandidate
from claim_detector.scanners.base import BaseScanner

_log = logging.getLogger("claim_detector.tiktok_scanner")

_CLIENT_KEY    = os.getenv("TIKTOK_CLIENT_KEY", "")
_CLIENT_SECRET = os.getenv("TIKTOK_CLIENT_SECRET", "")

# Keywords that signal a factcheckable claim
_CLAIM_KEYWORDS = [
    "breaking", "exposed", "proof", "confirmed", "sources say",
    "leaked", "cover-up", "they don't want you to know", "100%",
    "scientists say", "government admits", "study shows",
]


class TikTokScanner(BaseScanner):
    platform = "tiktok"

    def scan(self, *, limit: int = 50) -> List[ClaimCandidate]:
        if not _CLIENT_KEY or not _CLIENT_SECRET:
            _log.info("tiktok_scanner_stub_no_credentials")
            return self._stub_candidates(limit)
        try:
            return self._scan_api(limit=limit)
        except Exception as exc:
            _log.warning("tiktok_scanner_error error=%s", exc)
            return []

    def _scan_api(self, *, limit: int) -> List[ClaimCandidate]:
        import requests
        # Step 1: get client_credentials token
        resp = requests.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": _CLIENT_KEY,
                "client_secret": _CLIENT_SECRET,
                "grant_type": "client_credentials",
            },
            timeout=10,
        )
        resp.raise_for_status()
        token = resp.json().get("access_token", "")

        # Step 2: query research API for trending videos containing claim keywords
        query = " OR ".join(f'"{kw}"' for kw in _CLAIM_KEYWORDS[:5])
        resp2 = requests.post(
            "https://open.tiktokapis.com/v2/research/video/query/",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "query": {"and": [{"operation": "IN", "field_name": "keyword", "field_values": _CLAIM_KEYWORDS[:10]}]},
                "start_date": _days_ago(1),
                "end_date": _today(),
                "max_count": min(limit, 100),
                "fields": "id,desc,share_count,digg_count,comment_count,video_description",
            },
            timeout=15,
        )
        resp2.raise_for_status()
        videos = resp2.json().get("data", {}).get("videos", [])

        candidates = []
        for v in videos:
            text = v.get("video_description") or v.get("desc", "")
            if not text:
                continue
            virality = min(1.0, (v.get("share_count", 0) + v.get("digg_count", 0)) / 100_000)
            candidates.append(ClaimCandidate(
                raw_text          = text[:500],
                source_url        = f"https://www.tiktok.com/video/{v.get('id', '')}",
                platform          = "tiktok",
                virality_score    = virality,
                controversy_score = min(1.0, v.get("comment_count", 0) / 10_000),
            ))
        return candidates

    @staticmethod
    def _stub_candidates(limit: int) -> List[ClaimCandidate]:
        return [
            ClaimCandidate(
                raw_text       = f"[STUB] TikTok trending claim #{i+1} — replace with real API",
                source_url     = f"https://www.tiktok.com/stub/{i+1}",
                platform       = "tiktok",
                virality_score = 0.1 * (i + 1),
            )
            for i in range(min(3, limit))
        ]


def _today() -> str:
    from datetime import date
    return date.today().strftime("%Y%m%d")


def _days_ago(n: int) -> str:
    from datetime import date, timedelta
    return (date.today() - timedelta(days=n)).strftime("%Y%m%d")
