from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List
import json
import sys


@dataclass(frozen=True)
class ToolInvocation:
    argv: List[str]
    env: Dict[str, str] = field(default_factory=dict)


ToolHandler = Callable[[Dict[str, Any], Path], ToolInvocation]


def build_tool_registry() -> Dict[str, ToolHandler]:
    """
    Register mock handlers for v1 safe tool runner.

    Handlers intentionally execute deterministic Python one-liners so CI/tests
    can run without depending on external binaries.
    """
    return {
        "run_tests": _mock_handler(default_stdout="tests: ok\n"),
        "run_lint": _mock_handler(default_stdout="lint: ok\n"),
        "run_build": _mock_handler(default_stdout="build: ok\n", default_output_path="build.txt"),
    }


def _mock_handler(
    *,
    default_stdout: str,
    default_output_path: str | None = None,
) -> ToolHandler:
    def _handler(args: Dict[str, Any], workspace: Path) -> ToolInvocation:
        _ = workspace
        payload = {
            "sleep_s": float(args.get("sleep_s", 0.0) or 0.0),
            "stdout": str(args.get("stdout", default_stdout)),
            "stderr": str(args.get("stderr", "")),
            "fail": bool(args.get("fail", False)),
            "output_path": str(args.get("output_path", default_output_path or "")).strip(),
            "file_content": str(args.get("file_content", "mock output")),
        }
        script = (
            "import json, pathlib, sys, time\n"
            "cfg = json.loads(sys.argv[1])\n"
            "sleep_s = float(cfg.get('sleep_s', 0.0) or 0.0)\n"
            "if sleep_s > 0.0:\n"
            "    time.sleep(sleep_s)\n"
            "stdout = str(cfg.get('stdout', ''))\n"
            "stderr = str(cfg.get('stderr', ''))\n"
            "if stdout:\n"
            "    sys.stdout.write(stdout)\n"
            "if stderr:\n"
            "    sys.stderr.write(stderr)\n"
            "out_path = str(cfg.get('output_path', '') or '').strip()\n"
            "if out_path:\n"
            "    p = pathlib.Path(out_path)\n"
            "    p.parent.mkdir(parents=True, exist_ok=True)\n"
            "    p.write_text(str(cfg.get('file_content', '')), encoding='utf-8')\n"
            "sys.exit(1 if bool(cfg.get('fail', False)) else 0)\n"
        )
        return ToolInvocation(argv=[sys.executable, "-c", script, json.dumps(payload, ensure_ascii=True)])

    return _handler

