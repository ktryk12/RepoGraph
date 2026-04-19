"""Subprocess runners for each verification step. Each runner skips gracefully if tool missing."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from .models import VerificationStep

_TIMEOUT = 120  # seconds per step


def run_targeted_tests(repo_path: str, files: list[str]) -> VerificationStep:
    """targeted_test_runner — run pytest on files related to changed symbols."""
    if not shutil.which("pytest"):
        return _skip("test", "pytest")

    test_targets = _resolve_test_targets(repo_path, files)
    if not test_targets:
        return VerificationStep(name="test", status="skip", tool_used="pytest",
                                output="No test files found for changed symbols.")

    cmd = ["pytest", "--tb=short", "-q", "--no-header"] + test_targets
    return _run_step("test", "pytest", repo_path, cmd)


def run_lint(repo_path: str, files: list[str]) -> VerificationStep:
    """lint_runner — run ruff check on changed files."""
    if not shutil.which("ruff"):
        return _skip("lint", "ruff")
    py_files = [f for f in files if f.endswith(".py")]
    if not py_files:
        return VerificationStep(name="lint", status="skip", tool_used="ruff",
                                output="No Python files to lint.")
    cmd = ["ruff", "check", "--output-format=concise"] + py_files
    return _run_step("lint", "ruff", repo_path, cmd)


def run_type_check(repo_path: str, files: list[str]) -> VerificationStep:
    """type_check_runner — run mypy on changed files."""
    if not shutil.which("mypy"):
        return _skip("type_check", "mypy")
    py_files = [f for f in files if f.endswith(".py")]
    if not py_files:
        return VerificationStep(name="type_check", status="skip", tool_used="mypy",
                                output="No Python files to type-check.")
    cmd = ["mypy", "--ignore-missing-imports", "--no-error-summary"] + py_files
    return _run_step("type_check", "mypy", repo_path, cmd)


def run_static_analysis(repo_path: str, files: list[str]) -> VerificationStep:
    """static_analysis_runner — run bandit if available, else ruff with security rules."""
    py_files = [f for f in files if f.endswith(".py")]
    if not py_files:
        return VerificationStep(name="static_analysis", status="skip",
                                output="No Python files to analyse.")

    if shutil.which("bandit"):
        cmd = ["bandit", "-q", "-ll"] + py_files
        return _run_step("static_analysis", "bandit", repo_path, cmd)

    if shutil.which("ruff"):
        cmd = ["ruff", "check", "--select=S", "--output-format=concise"] + py_files
        return _run_step("static_analysis", "ruff[S]", repo_path, cmd)

    return _skip("static_analysis", "bandit/ruff")


def run_dependency_validator(repo_path: str, files: list[str]) -> VerificationStep:
    """dependency_validator — verify Python files import-parse without errors."""
    t0 = time.perf_counter()
    py_files = [f for f in files if f.endswith(".py")]
    if not py_files:
        return VerificationStep(name="dependency", status="skip",
                                output="No Python files to validate.")

    failures = []
    for filepath in py_files:
        full = Path(repo_path) / filepath if not Path(filepath).is_absolute() else Path(filepath)
        try:
            source = full.read_text(encoding="utf-8")
            compile(source, str(full), "exec")
        except SyntaxError as e:
            failures.append(f"{filepath}: SyntaxError at line {e.lineno}: {e.msg}")
        except FileNotFoundError:
            failures.append(f"{filepath}: file not found")

    duration_ms = int((time.perf_counter() - t0) * 1000)
    if failures:
        return VerificationStep(
            name="dependency", status="fail", tool_used="python-compile",
            output="\n".join(failures), failure_count=len(failures), duration_ms=duration_ms,
        )
    return VerificationStep(
        name="dependency", status="pass", tool_used="python-compile",
        output=f"All {len(py_files)} files compile cleanly.", duration_ms=duration_ms,
    )


def run_smoke_test(repo_path: str) -> VerificationStep:
    """smoke_test_runner — import the top-level package to verify no import errors."""
    t0 = time.perf_counter()
    init = Path(repo_path) / "__init__.py"
    package_name = Path(repo_path).name if init.exists() else None

    if not package_name:
        return VerificationStep(name="smoke", status="skip",
                                output="No top-level package found.")

    result = subprocess.run(
        ["python", "-c", f"import {package_name}; print('ok')"],
        capture_output=True, text=True, timeout=30, cwd=str(Path(repo_path).parent),
    )
    duration_ms = int((time.perf_counter() - t0) * 1000)
    if result.returncode == 0:
        return VerificationStep(name="smoke", status="pass", tool_used="python",
                                output=result.stdout.strip(), duration_ms=duration_ms)
    return VerificationStep(name="smoke", status="fail", tool_used="python",
                            output=result.stderr.strip(), failure_count=1, duration_ms=duration_ms)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_step(name: str, tool: str, cwd: str, cmd: list[str]) -> VerificationStep:
    t0 = time.perf_counter()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=_TIMEOUT, cwd=cwd,
        )
        duration_ms = int((time.perf_counter() - t0) * 1000)
        output = (result.stdout + result.stderr).strip()
        failure_count = output.count("\n") + 1 if result.returncode != 0 and output else 0
        status = "pass" if result.returncode == 0 else "fail"
        return VerificationStep(
            name=name, status=status, tool_used=tool,
            output=output[:4000], failure_count=failure_count, duration_ms=duration_ms,
        )
    except subprocess.TimeoutExpired:
        return VerificationStep(name=name, status="error", tool_used=tool,
                                output=f"Timed out after {_TIMEOUT}s")
    except Exception as exc:
        return VerificationStep(name=name, status="error", tool_used=tool, output=str(exc))


def _skip(name: str, tool: str) -> VerificationStep:
    return VerificationStep(name=name, status="skip", tool_used=tool, tool_missing=True,
                            output=f"{tool} not found in PATH — step skipped.")


def _resolve_test_targets(repo_path: str, changed_files: list[str]) -> list[str]:
    """Find test files that correspond to changed source files."""
    root = Path(repo_path)
    targets: list[str] = []

    for filepath in changed_files:
        stem = Path(filepath).stem
        # Look for test_<stem>.py or <stem>_test.py anywhere in tests/
        for candidate in root.rglob(f"test_{stem}.py"):
            targets.append(str(candidate))
        for candidate in root.rglob(f"{stem}_test.py"):
            targets.append(str(candidate))

    # Also include any direct test files in changed list
    for filepath in changed_files:
        p = Path(filepath)
        if p.stem.startswith("test_") or p.stem.endswith("_test"):
            full = root / filepath if not p.is_absolute() else p
            if full.exists():
                targets.append(str(full))

    return list(dict.fromkeys(targets))  # dedupe, preserve order
