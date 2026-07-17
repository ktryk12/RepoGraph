from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Mapping
import os
import json
import subprocess

from tools.contracts import ToolResult, ToolRunRef, ToolTiming
from tools.registry import ToolHandler, build_tool_registry


_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_MAX_BYTES = 50_000
_DEFAULT_TOOLS_MODE = "real"
_DEFAULT_MOCK_SEED = "0"


def feature_tool_runner_enabled(source: Mapping[str, str] | None = None, *, default: bool = True) -> bool:
    env = source if source is not None else os.environ
    return _parse_bool(env.get("FEATURE_TOOL_RUNNER"), default=default)


def feature_tool_evidence_gate_enabled(source: Mapping[str, str] | None = None, *, default: bool = False) -> bool:
    env = source if source is not None else os.environ
    return _parse_bool(env.get("FEATURE_TOOL_EVIDENCE_GATE"), default=default)


def tools_mode(source: Mapping[str, str] | None = None, *, default: str = _DEFAULT_TOOLS_MODE) -> str:
    env = source if source is not None else os.environ
    raw = str(env.get("TOOLS_MODE", default) or default).strip().lower()
    if raw in {"mock", "real", "replay"}:
        return raw
    return str(default).strip().lower()


def run_tool(
    tool_id: str,
    args: Dict[str, Any],
    workspace_dir: str | Path,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    *,
    registry: Dict[str, ToolHandler] | None = None,
) -> ToolResult:
    """
    Safe-ish tool wrapper.

    - workspace is always `<workspace_dir>/workspace`
    - process runs with cwd=workspace
    - stdout/stderr are captured and byte-capped
    - timeout returns ok=false + error_code=timeout
    - basic path-arg guard prevents escaping workspace
    """
    started_dt = _utc_now()
    tool_name = str(tool_id or "").strip()
    if not tool_name:
        return _error_result(
            tool_id="unknown",
            started_at=started_dt,
            error_code="invalid_tool_id",
            message="tool_id must be non-empty",
            workspace=None,
        )

    if not feature_tool_runner_enabled():
        return _error_result(
            tool_id=tool_name,
            started_at=started_dt,
            error_code="feature_disabled",
            message="FEATURE_TOOL_RUNNER=false",
            workspace=None,
        )

    reg = registry or build_tool_registry()
    handler = reg.get(tool_name)
    if handler is None:
        return _error_result(
            tool_id=tool_name,
            started_at=started_dt,
            error_code="tool_not_registered",
            message=f"tool '{tool_name}' is not registered",
            workspace=None,
        )

    timeout = max(0.01, float(timeout_s or _DEFAULT_TIMEOUT_S))
    cap = max(1, int(max_bytes or _DEFAULT_MAX_BYTES))
    workspace = _prepare_workspace(workspace_dir)
    mode = tools_mode()

    if mode == "mock":
        return _run_tool_mock_mode(
            tool_id=tool_name,
            args=dict(args or {}),
            started_at=started_dt,
            workspace=workspace,
        )
    if mode == "replay":
        return _run_tool_replay_mode(
            tool_id=tool_name,
            args=dict(args or {}),
            started_at=started_dt,
            workspace=workspace,
        )

    try:
        _validate_workspace_args(args or {}, workspace)
    except ValueError as exc:
        return _error_result(
            tool_id=tool_name,
            started_at=started_dt,
            error_code="workspace_violation",
            message=str(exc),
            workspace=workspace,
        )

    invocation = handler(dict(args or {}), workspace)
    argv = [str(part) for part in invocation.argv]
    if not argv:
        return _error_result(
            tool_id=tool_name,
            started_at=started_dt,
            error_code="invalid_invocation",
            message="tool invocation argv is empty",
            workspace=workspace,
        )

    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in (invocation.env or {}).items()})
    env["BABYAI_TOOL_WORKSPACE"] = workspace.as_posix()
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.run(
            argv,
            cwd=str(workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
            check=False,
        )
        stdout_text, stdout_truncated, stdout_bytes = _cap_bytes(proc.stdout, cap)
        stderr_text, stderr_truncated, stderr_bytes = _cap_bytes(proc.stderr, cap)
        finished_dt = _utc_now()
        ok = int(proc.returncode) == 0
        error_code = None if ok else "tool_failed"
        output = {
            "error_code": error_code,
            "exit_code": int(proc.returncode),
            "stdout": stdout_text,
            "stderr": stderr_text,
            "stdout_bytes": int(stdout_bytes),
            "stderr_bytes": int(stderr_bytes),
            "stdout_truncated": bool(stdout_truncated),
            "stderr_truncated": bool(stderr_truncated),
            "workspace": workspace.as_posix(),
        }
        return _result_from_output(
            tool_id=tool_name,
            ok=ok,
            output=output,
            started_at=started_dt,
            finished_at=finished_dt,
            error=(None if ok else "tool_failed"),
            backend="subprocess",
        )
    except subprocess.TimeoutExpired as exc:
        stdout_text, stdout_truncated, stdout_bytes = _cap_bytes(exc.stdout, cap)
        stderr_text, stderr_truncated, stderr_bytes = _cap_bytes(exc.stderr, cap)
        return _error_result(
            tool_id=tool_name,
            started_at=started_dt,
            error_code="timeout",
            message=f"tool '{tool_name}' exceeded timeout_s={timeout}",
            workspace=workspace,
            extra_output={
                "stdout": stdout_text,
                "stderr": stderr_text,
                "stdout_bytes": int(stdout_bytes),
                "stderr_bytes": int(stderr_bytes),
                "stdout_truncated": bool(stdout_truncated),
                "stderr_truncated": bool(stderr_truncated),
            },
        )


def _prepare_workspace(workspace_dir: str | Path) -> Path:
    base = Path(workspace_dir).resolve()
    workspace = (base / "workspace").resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def _validate_workspace_args(args: Dict[str, Any], workspace: Path) -> None:
    ws = workspace.resolve()
    for key, value in args.items():
        if not isinstance(value, str):
            continue
        name = str(key).strip().lower()
        if not _looks_like_path_key(name):
            continue
        candidate = Path(value)
        resolved = (ws / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if not _is_relative_to(resolved, ws):
            raise ValueError(f"path argument '{key}' escapes workspace: {value}")


def _looks_like_path_key(name: str) -> bool:
    return name == "path" or name.endswith("_path") or name.endswith("_file") or name.endswith("_dir")


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _cap_bytes(raw: bytes | None, max_bytes: int) -> tuple[str, bool, int]:
    data = raw or b""
    total = len(data)
    if total <= max_bytes:
        return data.decode("utf-8", errors="replace"), False, total
    clipped = data[:max_bytes]
    return clipped.decode("utf-8", errors="replace"), True, total


def _error_result(
    *,
    tool_id: str,
    started_at: datetime,
    error_code: str,
    message: str,
    workspace: Path | None,
    extra_output: Dict[str, Any] | None = None,
) -> ToolResult:
    finished_at = _utc_now()
    output = {
        "error_code": str(error_code),
        "message": str(message),
        "workspace": workspace.as_posix() if isinstance(workspace, Path) else None,
    }
    if isinstance(extra_output, dict):
        output.update(extra_output)
    return _result_from_output(
        tool_id=tool_id,
        ok=False,
        output=output,
        started_at=started_at,
        finished_at=finished_at,
        error=str(error_code),
        backend=None,
    )


def _result_from_output(
    *,
    tool_id: str,
    ok: bool,
    output: Dict[str, Any],
    started_at: datetime,
    finished_at: datetime,
    error: str | None,
    backend: str | None,
) -> ToolResult:
    started_text = _iso(started_at)
    finished_text = _iso(finished_at)
    duration_ms = _duration_ms(started_at, finished_at)
    artifact_ref = _artifact_ref(tool_id=tool_id, output=output)
    run_ref = ToolRunRef(tool_id=tool_id, artifact_ref=artifact_ref, manifest_ref=None)
    timing = ToolTiming(
        started_at=started_text,
        finished_at=finished_text,
        duration_ms=duration_ms,
    )
    return ToolResult(
        tool_id=tool_id,
        ok=bool(ok),
        output=dict(output),
        run_ref=run_ref,
        timing=timing,
        warnings=[],
        cost={},
        error=error,
        backend=backend,
    )


def _run_tool_mock_mode(
    *,
    tool_id: str,
    args: Dict[str, Any],
    started_at: datetime,
    workspace: Path,
) -> ToolResult:
    seed = str(os.environ.get("TOOLS_MOCK_SEED", _DEFAULT_MOCK_SEED))
    key_payload = {"seed": seed, "tool_id": tool_id, "args": args}
    replay_key = sha256(_stable_json(key_payload).encode("utf-8", errors="replace")).hexdigest()
    fail = bool(args.get("fail", False))
    output = {
        "mode": "mock",
        "seed": seed,
        "replay_key": replay_key,
        "error_code": (None if not fail else "tool_failed"),
        "stdout": str(args.get("stdout", f"{tool_id}:mock\n")),
        "stderr": str(args.get("stderr", "")),
        "stdout_bytes": len(str(args.get("stdout", f"{tool_id}:mock\n")).encode("utf-8")),
        "stderr_bytes": len(str(args.get("stderr", "")).encode("utf-8")),
        "stdout_truncated": False,
        "stderr_truncated": False,
        "exit_code": 0 if not fail else 1,
    }
    return _result_from_output(
        tool_id=tool_id,
        ok=not fail,
        output=output,
        started_at=started_at,
        finished_at=_utc_now(),
        error=None if not fail else "tool_failed",
        backend="mock",
    )


def _run_tool_replay_mode(
    *,
    tool_id: str,
    args: Dict[str, Any],
    started_at: datetime,
    workspace: Path,
) -> ToolResult:
    replay_path = _resolve_replay_path(tool_id=tool_id, args=args)
    if replay_path is None or not replay_path.exists():
        return _error_result(
            tool_id=tool_id,
            started_at=started_at,
            error_code="replay_fixture_missing",
            message=f"replay fixture not found for tool '{tool_id}'",
            workspace=workspace,
        )

    try:
        payload = json.loads(replay_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _error_result(
            tool_id=tool_id,
            started_at=started_at,
            error_code="replay_fixture_invalid",
            message=f"invalid replay fixture: {exc}",
            workspace=workspace,
        )

    try:
        result = _resolve_replay_result(tool_id=tool_id, args=args, payload=payload)
        return ToolResult(
            tool_id=result.tool_id,
            ok=bool(result.ok),
            output=dict(result.output),
            run_ref=result.run_ref,
            timing=result.timing,
            warnings=list(result.warnings),
            cost=dict(result.cost),
            error=result.error,
            backend="replay",
            schema_version=int(result.schema_version),
        )
    except Exception as exc:
        return _error_result(
            tool_id=tool_id,
            started_at=started_at,
            error_code="replay_fixture_invalid",
            message=f"replay fixture parse failed: {exc}",
            workspace=workspace,
        )


def _resolve_replay_path(*, tool_id: str, args: Dict[str, Any]) -> Path | None:
    explicit = args.get("replay_path")
    if isinstance(explicit, str) and explicit.strip():
        return Path(explicit.strip())

    base = str(os.environ.get("TOOLS_REPLAY_DIR", "") or "").strip()
    if not base:
        return None
    return Path(base) / f"{tool_id}.json"


def _resolve_replay_result(*, tool_id: str, args: Dict[str, Any], payload: Any) -> ToolResult:
    if isinstance(payload, dict) and "tool_id" in payload and "run_ref" in payload and "timing" in payload:
        return ToolResult.from_dict(payload)

    replay_key = _default_replay_key(tool_id=tool_id, args=args)

    # Format A: {"records": {"<replay_key>": <tool_result_dict>, "default": <tool_result_dict>}}
    if isinstance(payload, dict) and isinstance(payload.get("records"), dict):
        records = payload["records"]
        row = records.get(replay_key, records.get("default"))
        if isinstance(row, dict):
            return ToolResult.from_dict(row)

    # Format B: [{"replay_key": "...", "result": <tool_result_dict>}, ...]
    if isinstance(payload, list):
        for row in payload:
            if not isinstance(row, dict):
                continue
            row_key = str(row.get("replay_key", "") or "").strip()
            if row_key == replay_key and isinstance(row.get("result"), dict):
                return ToolResult.from_dict(row["result"])
        for row in payload:
            if not isinstance(row, dict):
                continue
            if str(row.get("replay_key", "") or "").strip() == "default" and isinstance(row.get("result"), dict):
                return ToolResult.from_dict(row["result"])

    raise ValueError("no matching replay result")


def _default_replay_key(*, tool_id: str, args: Dict[str, Any]) -> str:
    if isinstance(args.get("replay_key"), str) and str(args.get("replay_key")).strip():
        return str(args.get("replay_key")).strip()
    normalized_args = {k: v for k, v in args.items() if k not in {"replay_path", "replay_key"}}
    payload = {"tool_id": tool_id, "args": normalized_args}
    return sha256(_stable_json(payload).encode("utf-8", errors="replace")).hexdigest()


def _artifact_ref(*, tool_id: str, output: Dict[str, Any]) -> str:
    payload = f"{tool_id}|{_stable_json(output)}".encode("utf-8", errors="replace")
    return f"artifact:sha256:{sha256(payload).hexdigest()}"


def _stable_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _duration_ms(started_at: datetime, finished_at: datetime) -> float:
    return max(0.0, (finished_at - started_at).total_seconds() * 1000.0)


def _parse_bool(raw: Any, *, default: bool) -> bool:
    if raw is None:
        return bool(default)
    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)
