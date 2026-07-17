"""claim_detector/api.py — FastAPI health + status endpoint (port 8120)."""
from __future__ import annotations

import os
from fastapi import FastAPI

app = FastAPI(title="claim-detector", version="1.0.0")

_PORT = int(os.getenv("CLAIM_DETECTOR_PORT", "8120"))


@app.get("/health")
def health():
    return {"status": "ok", "service": "claim-detector"}


@app.get("/status")
def status():
    from claim_detector.runner import _SCAN_INTERVAL, _MIN_SCORE
    return {
        "scan_interval_seconds": _SCAN_INTERVAL,
        "min_composite_score":   _MIN_SCORE,
        "platforms":             ["tiktok", "x", "news", "youtube"],
    }
