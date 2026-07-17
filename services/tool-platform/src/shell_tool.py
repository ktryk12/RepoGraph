from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import shlex
import subprocess
import sys
from typing import Any

from babyai.tools.result import ToolResult, duration_ms, ensure_audit_sink, log_tool_call


_DESTRUCTIVE_MARKERS = (
    " rm ",
    " rm-",
    " rmdir ",
    " del ",
    " format ",
    " mkfs ",
    " shutdown ",
    " reboot ",
    " dd ",
    " git reset --hard",
)
_WRITE_MARKERS = (
    " touch ",
    " mkdir ",
    " cp ",
    " mv ",
    " tee ",
    " sed -i",
    " >",
    " >>",
)


class ShellTool:
    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        working_dir: str | Path | None = None,
        timeout: float = 30.0,
    ) -> None:
        clean_command = str(command or "").strip()
        if not clean_command:
            raise ValueError("command must be non-empty")
        self.command = clean_command
        self.args = [str(item) for item in list(args or [])]
        self.working_dir = str(working_dir or ".")
        self.timeout = max(0.01, float(timeout or 30.0))

    def permission_level(self) -> str:
        material = f" {self.command} {' '.join(self.args)} ".lower()
        if any(marker in material for marker in _DESTRUCTIVE_MARKERS):
            return "high"
        if any(marker in material for marker in _WRITE_MARKERS):
            return "medium"
        return "low"

    def execute(
        self,
        *,
        project_id: str,
        domain: str,
        memory_ref: Any,
        agent_id: str | None = None,
    ) -> ToolResult:
        sink = ensure_audit_sink(memory_ref, project_id=project_id, domain=domain)
        started = datetime.now(timezone.utc)
        permission = self.permission_level()
        request_payload = {
            "command": self.command,
            "args": list(self.args),
            "working_dir": self.working_dir,
            "timeout": self.timeout,
        }

        cwd = Path(self.working_dir).expanduser().resolve()
        cwd.mkdir(parents=True, exist_ok=True)
        shell_mode, invocation = _build_invocation(self.command, self.args)
        if shell_mode:
            invocation_display = str(invocation)
        else:
            invocation_display = " ".join(str(item) for item in invocation)

        result: ToolResult
        try:
            proc = subprocess.run(
                invocation,
                cwd=cwd.as_posix(),
                shell=shell_mode,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            finished = datetime.now(timezone.utc)
            ok = int(proc.returncode) == 0
            result = ToolResult(
                tool_name="shell_tool",
                tool_type="shell",
                permission_level=permission,
                ok=ok,
                output={
                    "command": invocation_display,
                    "working_dir": cwd.as_posix(),
                    "stdout": str(proc.stdout or ""),
                    "stderr": str(proc.stderr or ""),
                    "exit_code": int(proc.returncode),
                },
                error=None if ok else "tool_failed",
                started_at=started.isoformat().replace("+00:00", "Z"),
                finished_at=finished.isoformat().replace("+00:00", "Z"),
                duration_ms=duration_ms(started_at=started, finished_at=finished),
            )
        except subprocess.TimeoutExpired as exc:
            finished = datetime.now(timezone.utc)
            result = ToolResult(
                tool_name="shell_tool",
                tool_type="shell",
                permission_level=permission,
                ok=False,
                output={
                    "command": invocation_display,
                    "working_dir": cwd.as_posix(),
                    "stdout": (exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")),
                    "stderr": (exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")),
                    "exit_code": None,
                },
                error="timeout",
                started_at=started.isoformat().replace("+00:00", "Z"),
                finished_at=finished.isoformat().replace("+00:00", "Z"),
                duration_ms=duration_ms(started_at=started, finished_at=finished),
            )

        log_tool_call(
            sink=sink,
            project_id=project_id,
            domain=domain,
            tool_name="shell_tool",
            tool_type="shell",
            permission_level=permission,
            request=request_payload,
            result=result,
            agent_id=agent_id,
        )
        return result


def _build_invocation(command: str, args: list[str]) -> tuple[bool, list[str] | str]:
    cmd_path = Path(command)
    if cmd_path.suffix.lower() == ".py" and cmd_path.exists():
        return False, [sys.executable, command, *args]

    if command.lower().startswith("python ") and not args:
        return True, command

    special_chars = {"|", "&", ";", "<", ">", "(", ")"}
    if any(char in command for char in special_chars):
        joined = _join_shell_command(command, args)
        return True, joined

    return False, [command, *args]


def _join_shell_command(command: str, args: list[str]) -> str:
    if not args:
        return str(command)
    if os.name == "nt":
        return " ".join([str(command), subprocess.list2cmdline(list(args))]).strip()
    return " ".join([str(command), shlex.join(list(args))]).strip()
