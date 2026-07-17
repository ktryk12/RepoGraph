"""services/claim-detector/main.py — entry point."""
from __future__ import annotations

import logging
import os
import threading
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
_log = logging.getLogger("claim_detector")

_PORT = int(os.getenv("CLAIM_DETECTOR_PORT", "8120"))


def main() -> None:
    from claim_detector.runner import ClaimDetectorRunner
    from claim_detector.api import app

    runner = ClaimDetectorRunner()
    scan_thread = threading.Thread(target=runner.run_forever, daemon=True, name="scan-loop")
    scan_thread.start()
    _log.info("claim_detector scan loop started")

    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="warning")


if __name__ == "__main__":
    main()
