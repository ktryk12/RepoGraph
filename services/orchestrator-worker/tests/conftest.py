"""Conftest for orchestrator-worker service tests.

Adds services/orchestrator-worker/src/ to sys.path so tests can do:
    from orchestrator_worker import OrchestratorWorker
    monkeypatch.setattr("orchestrator_worker.run_episode", ...)

Also ensures repo root is on sys.path so shared test utilities
like tests.approval_gate_testutils are importable.
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_src = os.path.join(os.path.dirname(_here), "src")
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(_here)))

if _src not in sys.path:
    sys.path.insert(0, _src)
# Add tests dir so local test utilities (approval_gate_testutils, etc.) are importable
if _here not in sys.path:
    sys.path.insert(0, _here)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# Keep AUTO_APPROVE set for e2e tests
os.environ.setdefault("AUTO_APPROVE", "true")
