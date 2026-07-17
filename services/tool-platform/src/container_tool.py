from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
import json
import subprocess
from typing import Any, Callable
from uuid import uuid4

from babyai.tools.result import ToolResult, duration_ms, ensure_audit_sink, log_tool_call


class ContainerTool:
    def __init__(
        self,
        image: str,
        command: str | list[str],
        env: dict[str, str] | None = None,
        resource_limits: dict[str, Any] | None = None,
        timeout: float = 120.0,
        runner: Callable[[dict[str, Any]], Any] | None = None,
        cleanup_runner: Callable[[str], None] | None = None,
    ) -> None:
        clean_image = str(image or "").strip()
        if not clean_image:
            raise ValueError("image must be non-empty")
        self.image = clean_image
        self.command = list(command) if isinstance(command, list) else str(command or "").strip()
        self.env = {str(k): str(v) for k, v in dict(env or {}).items()}
        self.resource_limits = dict(resource_limits or {})
        self.timeout = max(0.01, float(timeout or 120.0))
        self._runner = runner
        self._cleanup_runner = cleanup_runner

    def permission_level(self) -> str:
        return "medium"

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
        container_name = f"babyai-tool-{uuid4().hex[:12]}"
        permission = self.permission_level()
        request_payload = {
            "image": self.image,
            "command": self.command,
            "env": dict(self.env),
            "resource_limits": dict(self.resource_limits),
            "timeout": self.timeout,
            "container_name": container_name,
        }

        cleanup_completed = False
        timed_out = False
        exit_code: int | None = None
        stdout = ""
        stderr = ""
        error: str | None = None
        ok = False
        try:
            if callable(self._runner):
                response = _run_with_timeout(
                    timeout=self.timeout,
                    fn=lambda: self._runner(
                        {
                            "image": self.image,
                            "command": self.command,
                            "env": dict(self.env),
                            "resource_limits": dict(self.resource_limits),
                            "container_name": container_name,
                        }
                    ),
                )
                exit_code, stdout, stderr = _normalize_runner_response(response)
                ok = exit_code == 0
                error = None if ok else "container_failed"
            else:
                proc = subprocess.run(
                    _docker_run_argv(
                        image=self.image,
                        command=self.command,
                        env=self.env,
                        resource_limits=self.resource_limits,
                        container_name=container_name,
                    ),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                )
                exit_code = int(proc.returncode)
                stdout = str(proc.stdout or "")
                stderr = str(proc.stderr or "")
                ok = exit_code == 0
                error = None if ok else "container_failed"
        except FuturesTimeoutError:
            timed_out = True
            error = "timeout"
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            stdout = _as_text(exc.stdout)
            stderr = _as_text(exc.stderr)
            error = "timeout"
        except Exception as exc:
            error = f"container_error:{exc}"
        finally:
            cleanup_completed = _cleanup_container(container_name=container_name, cleanup_runner=self._cleanup_runner)

        finished = datetime.now(timezone.utc)
        result = ToolResult(
            tool_name="container_tool",
            tool_type="container",
            permission_level=permission,
            ok=bool(ok and not timed_out),
            output={
                "image": self.image,
                "container_name": container_name,
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "resource_limits": dict(self.resource_limits),
                "timed_out": timed_out,
                "cleanup_completed": cleanup_completed,
            },
            error=error,
            started_at=started.isoformat().replace("+00:00", "Z"),
            finished_at=finished.isoformat().replace("+00:00", "Z"),
            duration_ms=duration_ms(started_at=started, finished_at=finished),
        )
        log_tool_call(
            sink=sink,
            project_id=project_id,
            domain=domain,
            tool_name="container_tool",
            tool_type="container",
            permission_level=permission,
            request=request_payload,
            result=result,
            agent_id=agent_id,
        )
        return result


def _run_with_timeout(*, timeout: float, fn: Callable[[], Any]) -> Any:
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fn)
        return future.result(timeout=float(timeout))


def _normalize_runner_response(response: Any) -> tuple[int, str, str]:
    if isinstance(response, dict):
        return (
            int(response.get("exit_code", 0)),
            str(response.get("stdout", "")),
            str(response.get("stderr", "")),
        )
    if isinstance(response, tuple):
        rows = list(response)
        if len(rows) >= 3:
            return int(rows[0]), str(rows[1]), str(rows[2])
        if len(rows) == 2:
            return int(rows[0]), str(rows[1]), ""
    if response is None:
        return 0, "", ""
    return 0, json.dumps(response, ensure_ascii=True, default=str), ""


def _docker_run_argv(
    *,
    image: str,
    command: str | list[str],
    env: dict[str, str],
    resource_limits: dict[str, Any],
    container_name: str,
) -> list[str]:
    argv = ["docker", "run", "--name", container_name, "--rm"]
    cpu = resource_limits.get("cpu")
    if cpu is not None:
        argv.extend(["--cpus", str(cpu)])
    memory = resource_limits.get("memory")
    if memory is not None:
        argv.extend(["--memory", str(memory)])
    network = resource_limits.get("network")
    if network is not None:
        argv.extend(["--network", str(network)])
    for key, value in env.items():
        argv.extend(["-e", f"{key}={value}"])
    argv.append(str(image))
    if isinstance(command, list):
        argv.extend(str(item) for item in command)
    else:
        clean = str(command or "").strip()
        if clean:
            argv.extend(["sh", "-lc", clean])
    return argv


def _cleanup_container(*, container_name: str, cleanup_runner: Callable[[str], None] | None) -> bool:
    if callable(cleanup_runner):
        cleanup_runner(str(container_name))
        return True
    try:
        subprocess.run(
            ["docker", "rm", "-f", str(container_name)],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
        return True
    except Exception:
        return False


def _as_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")
