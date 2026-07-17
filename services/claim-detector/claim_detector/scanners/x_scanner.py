"""
X (Twitter) v2 API scanner.
Requires: TWITTER_BEARER_TOKEN
Falls back to stub if not configured.
"""
from __future__ import annotations

import logging
import os
from typing import List

from claim_detector.models import ClaimCandidate
from claim_detector.scanners.base import BaseScanner

_log = logging.getLogger("claim_detector.x_scanner")

_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN", "")

_SEARCH_QUERY = (
    "(breaking OR exposed OR proof OR confirmed OR leaked OR cover-up) "
    "-is:retweet lang:en -is:reply"
)
_MIN_RETWEETS = 100


class XScanner(BaseScanner):
    platform = "x"

    def scan(self, *, limit: int = 50) -> List[ClaimCandidate]:
        if not _BEARER_TOKEN:
            _log.info("x_scanner_stub_no_token")
            return self._stub_candidates(limit)
        try:
            return self._scan_api(limit=limit)
        except Exception as exc:
            _log.warning("x_scanner_error error=%s", exc)
            return []

    def _scan_api(self, *, limit: int) -> List[ClaimCandidate]:
        import requests
        resp = requests.get(
            "https://api.twitter.com/2/tweets/search/recent",
            headers={"Authorization": f"Bearer {_BEARER_TOKEN}"},
            params={
                "query":        _SEARCH_QUERY,
                "max_results":  min(limit, 100),
                "tweet.fields": "public_metrics,created_at,author_id",
                "expansions":   "author_id",
            },
            timeout=10,
        )
        resp.raise_for_status()
        tweets = resp.json().get("data", [])

        candidates = []
        for t in tweets:
            metrics = t.get("public_metrics", {})
            retweets = metrics.get("retweet_count", 0)
            likes    = metrics.get("like_count", 0)
            if retweets < _MIN_RETWEETS:
                continue
            virality = min(1.0, (retweets + likes) / 50_000)
            controversy = min(1.0, metrics.get("reply_count", 0) / 5_000)
            candidates.append(ClaimCandidate(
                raw_text          = t.get("text", "")[:500],
                source_url        = f"https://x.com/i/web/status/{t.get('id', '')}",
                platform          = "x",
                virality_score    = virality,
                controversy_score = controversy,
            ))
        return candidates

    @staticmethod
    def _stub_candidates(limit: int) -> List[ClaimCandidate]:
        return [
            ClaimCandidate(
                raw_text       = f"[STUB] X trending claim #{i+1} — replace with real API",
                source_url     = f"https://x.com/stub/{i+1}",
                platform       = "x",
                virality_score = 0.1 * (i + 1),
            )
            for i in range(min(3, limit))
        ]
