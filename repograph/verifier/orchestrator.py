"""PatchVerifier orchestrator — runs all verification steps and feeds back to TaskMemory."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from repograph.graph.factory import GraphStore

from .models import VerificationResult
from .runners import (
    run_dependency_validator,
    run_lint,
    run_smoke_test,
    run_static_analysis,
    run_targeted_tests,
    run_type_check,
)

_STEP_ORDER = ("dependency", "lint", "type_check", "test", "static_analysis", "smoke")


def verify(
    repo_path: str,
    files: list[str],
    symbols: list[str] | None = None,
    store: GraphStore | None = None,
    task_id: str | None = None,
    steps: list[str] | None = None,
) -> VerificationResult:
    """
    Run all enabled verification steps and return a VerificationResult.

    Args:
        repo_path: absolute path to repository root
        files: relative file paths that were changed
        symbols: symbol IDs that were changed (for context)
        store: graph store — used for memory feedback if task_id is given
        task_id: if given, update TaskMemory with verification outcome
        steps: subset of steps to run; runs all if None
    """
    if not Path(repo_path).is_dir():
        return VerificationResult(
            verification_id=f"verify:{uuid.uuid4()}",
            repo_path=repo_path,
            overall_status="error",
            verified_at=_now(),
        )

    enabled = set(steps or _STEP_ORDER)
    t0 = time.perf_counter()
    step_results = []

    if "dependency" in enabled:
        step_results.append(run_dependency_validator(repo_path, files))
    if "lint" in enabled:
        step_results.append(run_lint(repo_path, files))
    if "type_check" in enabled:
        step_results.append(run_type_check(repo_path, files))
    if "test" in enabled:
        step_results.append(run_targeted_tests(repo_path, files))
    if "static_analysis" in enabled:
        step_results.append(run_static_analysis(repo_path, files))
    if "smoke" in enabled:
        step_results.append(run_smoke_test(repo_path))

    overall = _compute_overall(step_results)
    duration_ms = int((time.perf_counter() - t0) * 1000)

    result = VerificationResult(
        verification_id=f"verify:{uuid.uuid4()}",
        task_id=task_id,
        repo_path=repo_path,
        symbols_verified=symbols or [],
        files_verified=files,
        steps=step_results,
        overall_status=overall,
        duration_ms=duration_ms,
        verified_at=_now(),
    )

    if store and task_id:
        _feedback_to_memory(store, task_id, result)

    return result


def _compute_overall(steps) -> str:
    statuses = {s.status for s in steps}
    if "fail" in statuses or "error" in statuses:
        all_fail = all(s.status in {"fail", "error", "skip"} for s in steps)
        return "fail" if all_fail or "fail" in statuses else "partial"
    if all(s.status in {"pass", "skip"} for s in steps):
        return "pass"
    return "partial"


def _feedback_to_memory(store: GraphStore, task_id: str, result: VerificationResult) -> None:
    """verification_to_memory_feedback — write verification outcome into TaskMemory."""
    from repograph.memory import store as mem_store
    from repograph.memory.models import PrecisionSignals

    passed = result.overall_status == "pass"
    signals = PrecisionSignals(verification_passed=passed)
    mem_store.update_signals(store, task_id, signals)

    # Record failing steps as test failures
    if not passed:
        from repograph.memory.models import TestFailureRecord
        for step in result.steps:
            if step.status in {"fail", "error"}:
                failure = TestFailureRecord(
                    test_symbol=f"verifier::{step.name}",
                    failure_message=step.output[:500],
                    recorded_at=result.verified_at,
                )
                mem_store.add_test_failure(store, task_id, failure)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
