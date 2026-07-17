"""Conftest for planner service tests.

Adds services/planner/src/ and repo root to sys.path so tests can import:
    from application.use_cases import PlannerService
    from planner.application.use_cases import PlannerService  (via shim)
    import planner.main  (via shim)
"""
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
_src = os.path.join(os.path.dirname(_here), "src")
_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(_here)))

for _p in [_src, _repo_root]:
    if _p not in sys.path:
        sys.path.insert(0, _p)
