"""
Migration shim — authoritative code at services/orchestrator-worker/src/orchestrator_worker.py.

Kept for backward compatibility with repo-root main.py and scripts/run_orchestrator_worker.py.
Tests have been moved to services/orchestrator-worker/tests/ and import orchestrator_worker directly.
"""
import sys as _sys
import os as _os

_src = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "services", "orchestrator-worker", "src",
)
if _src not in _sys.path:
    _sys.path.insert(0, _src)

from orchestrator_worker import *  # noqa: F401, F403, E402
from orchestrator_worker import (  # noqa: F401, E402
    OrchestratorWorker,
    ApprovalMissingError,
    run_episode,
    load_truth_pack,
    _SERVICE_NAME,
    _EPISODE_REQUESTED_V1_SCHEMA_PATH,
)
