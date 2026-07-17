"""
claim_detector/runner.py — main scan loop.

Scan interval: CLAIM_SCAN_INTERVAL_SECONDS (default 300 = 5 min).
Per run: alle scannere → rank → dedup → emit.
Min composite score: CLAIM_MIN_SCORE (default 0.15).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import List

from claim_detector.deduper import Deduper
from claim_detector.emitter import ClaimEmitter
from claim_detector.models import DetectedClaim
from claim_detector.ranker import rank
from claim_detector.scanners.base import BaseScanner
from claim_detector.scanners.news_scanner import NewsScanner
from claim_detector.scanners.tiktok_scanner import TikTokScanner
from claim_detector.scanners.x_scanner import XScanner
from claim_detector.scanners.youtube_scanner import YouTubeScanner

_log = logging.getLogger("claim_detector.runner")

_SCAN_INTERVAL = int(os.getenv("CLAIM_SCAN_INTERVAL_SECONDS", "300"))
_MIN_SCORE     = float(os.getenv("CLAIM_MIN_SCORE", "0.15"))
_LIMIT_PER_SCANNER = int(os.getenv("CLAIM_LIMIT_PER_SCANNER", "50"))


class ClaimDetectorRunner:
    def __init__(self) -> None:
        self._scanners: List[BaseScanner] = [
            TikTokScanner(),
            XScanner(),
            NewsScanner(),
            YouTubeScanner(),
        ]
        self._deduper = Deduper()
        self._emitter = ClaimEmitter()
        self._stop    = threading.Event()

    def run_once(self) -> int:
        candidates = []
        for scanner in self._scanners:
            try:
                batch = scanner.scan(limit=_LIMIT_PER_SCANNER)
                _log.debug("scanner=%s found=%d", scanner.platform, len(batch))
                candidates.extend(batch)
            except Exception as exc:
                _log.error("scanner_error platform=%s error=%s", scanner.platform, exc)

        ranked   = rank(candidates)
        emitted  = 0
        skipped  = 0

        for claim in ranked:
            if claim.composite_score < _MIN_SCORE:
                break  # sorted descending — rest will be lower
            if self._deduper.is_duplicate(claim.raw_text):
                skipped += 1
                continue
            self._deduper.mark_seen(claim.raw_text)
            self._emitter.emit(claim)
            emitted += 1

        self._emitter.flush()
        _log.info(
            "scan_complete total=%d ranked=%d emitted=%d skipped_dup=%d",
            len(candidates), len(ranked), emitted, skipped,
        )
        return emitted

    def run_forever(self) -> None:
        _log.info("claim_detector starting scan_interval=%ds", _SCAN_INTERVAL)
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:
                _log.error("runner_error error=%s", exc)
            self._stop.wait(timeout=_SCAN_INTERVAL)

    def stop(self) -> None:
        self._stop.set()
