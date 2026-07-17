from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional
import os
import subprocess
import sys
import time

from tools.base import ToolBudget, ToolResult, artifact_ref_for_bytes, clamp_bytes


class RunTestsTool:
    name = "run_tests"

    def __init__(self, root: Path | None = None, *, python_exe: Optional[str] = None) -> None:
        self.root = root or Path(__file__).resolve().parents[1]
        self.python_exe = python_exe or sys.executable

    def run(self, request: Dict[str, Any], *, budget: ToolBudget) -> ToolResult:
        args = request.get("args")
        timeout_seconds = request.get("timeout_seconds")
        if args is None:
            args_list: List[str] = ["-q"]
        elif isinstance(args, str):
            args_list = [args]
        elif isinstance(args, list):
            args_list = [str(a) for a in args]
        else:
            return ToolResult(
                self.name,
                False,
                {"error": "invalid_args"},
                warnings=["invalid_args"],
            )

        workdir = request.get("workdir")
        if isinstance(workdir, str) and workdir.strip():
            cwd = Path(workdir)
            if not cwd.is_absolute():
                cwd = (self.root / cwd).resolve()
        else:
            cwd = self.root

        cmd = [self.python_exe, "-m", "pytest"] + args_list
        env = os.environ.copy()
        env["PYTHONHASHSEED"] = "0"
        env["PYTHONUTF8"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"

        start = time.monotonic()
        timed_out = False
        stdout = b""
        stderr = b""
        return_code = 1
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=int(timeout_seconds) if timeout_seconds else None,
            )
            return_code = proc.returncode
            stdout = proc.stdout or b""
            stderr = proc.stderr or b""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = exc.stdout or b""
            stderr = exc.stderr or b""
        duration_ms = int((time.monotonic() - start) * 1000)

        combined = stdout + stderr
        text = combined.decode("utf-8", errors="replace")
        from babyai_shared.privacy.redaction import redact_text

        text = redact_text(text)
        clipped, truncated = clamp_bytes(text, budget.max_bytes)
        result_ref = artifact_ref_for_bytes(clipped.encode("utf-8"))

        warnings = []
        if truncated:
            warnings.append("truncated_output")
        if timed_out:
            warnings.append("timeout")
        if return_code != 0 and not timed_out:
            warnings.append("tests_failed")

        failed_tests = _extract_failed_tests(text)
        passed = (return_code == 0) and not timed_out

        output = {
            "result_ref": result_ref,
            "log_ref": result_ref,
            "exit_code": return_code,
            "duration_ms": duration_ms,
            "args": args_list,
            "workdir": str(cwd),
            "truncated": truncated,
            "passed": passed,
            "failed_tests": failed_tests,
            "timed_out": timed_out,
            "timeout_seconds": int(timeout_seconds) if timeout_seconds else None,
        }
        ok = passed
        if timed_out:
            output["error"] = "timeout"
        return ToolResult(self.name, ok, output, artifact_ref=result_ref, warnings=warnings)


def _extract_failed_tests(text: str) -> List[str]:
    failed: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("FAILED "):
            failed.append(line.replace("FAILED ", "").strip())
        elif line.startswith("ERROR "):
            failed.append(line.replace("ERROR ", "").strip())
    # Deduplicate while preserving order
    seen: set[str] = set()
    ordered: List[str] = []
    for item in failed:
        if item not in seen:
            ordered.append(item)
            seen.add(item)
    return ordered
