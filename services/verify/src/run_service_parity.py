from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List

from services.aesa.bootstrap.system_bootstrap import bootstrap_system
from services.aesa.service.expert_api import compare_in_process_and_http


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_PATH = REPO_ROOT / "eval" / "aesa" / "service_parity_tasks.jsonl"


def main() -> int:
    p = argparse.ArgumentParser(description="Compare AESA in-process and HTTP execution parity.")
    p.add_argument("--tasks", default=str(DEFAULT_TASKS_PATH), help="Task JSONL path (task.schema compatible).")
    p.add_argument("--max-tasks", type=int, default=None, help="Limit tasks to execute.")
    p.add_argument("--out", default=None, help="Output report JSON path.")
    p.add_argument(
        "--require-http",
        action="store_true",
        help="Fail when FastAPI HTTP runtime is unavailable.",
    )
    p.add_argument(
        "--print-mismatches",
        action="store_true",
        help="Print mismatch summaries to stdout.",
    )
    args = p.parse_args()

    run_id = _run_id()
    out_path = Path(args.out) if args.out else Path("artifacts") / "aesa_service_parity" / f"{run_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = run_service_parity(
        tasks_path=Path(args.tasks),
        max_tasks=args.max_tasks,
        require_http=bool(args.require_http),
        print_mismatches=bool(args.print_mismatches),
        run_id=run_id,
    )
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[service-parity] wrote {out_path}")
    print(
        "[service-parity] "
        f"matched={payload.get('matched_tasks', 0)}/{payload.get('tasks_executed', 0)} "
        f"used_http={payload.get('used_http_runtime', False)}"
    )
    if payload.get("http_unavailable") and args.require_http:
        print("[service-parity] gate_failed: HTTP runtime unavailable")
        return 3
    if not payload.get("all_matched", False):
        print("[service-parity] gate_failed: mismatch detected")
        return 2
    return 0


def run_service_parity(
    *,
    tasks_path: Path,
    max_tasks: int | None,
    require_http: bool,
    print_mismatches: bool,
    run_id: str | None = None,
) -> Dict[str, Any]:
    rows = _load_tasks(tasks_path)
    if max_tasks is not None and max_tasks > 0:
        rows = rows[: int(max_tasks)]

    boot = bootstrap_system()
    compared: List[Dict[str, Any]] = []
    http_unavailable = False
    used_http_runtime = True

    for idx, task in enumerate(rows, start=1):
        task_id = str(task.get("task_id") or f"SVC-{idx:03d}")
        try:
            cmp_row = compare_in_process_and_http(
                task,
                registry=boot.swarm_registry,
                hardware_plan=boot.hardware_plan,
                allow_simulated_http=not require_http,
            )
            http_transport = str((cmp_row.get("http") or {}).get("transport", ""))
            if http_transport == "http_simulated":
                used_http_runtime = False
            item = {
                "task_id": task_id,
                "match": bool(cmp_row.get("match")),
                "http_transport": http_transport,
            }
            if not item["match"]:
                item["normalized_in_process"] = cmp_row.get("normalized_in_process")
                item["normalized_http"] = cmp_row.get("normalized_http")
            compared.append(item)
        except RuntimeError as exc:
            msg = str(exc)
            if "HTTP execution requires fastapi" in msg or "fastapi.testclient" in msg:
                http_unavailable = True
                used_http_runtime = False
                compared.append(
                    {
                        "task_id": task_id,
                        "match": False,
                        "error": "http_unavailable",
                        "msg": msg,
                    }
                )
                continue
            compared.append(
                {
                    "task_id": task_id,
                    "match": False,
                    "error": "execution_failed",
                    "msg": msg,
                }
            )

    mismatches = [r for r in compared if not bool(r.get("match"))]
    if print_mismatches:
        for row in mismatches:
            print(
                "[service-parity] mismatch "
                f"task_id={row.get('task_id')} "
                f"error={row.get('error', 'parity_diff')}"
            )

    all_matched = bool(compared) and not mismatches
    if http_unavailable and require_http:
        all_matched = False

    return {
        "run_id": run_id or _run_id(),
        "generated_at": _utc_now(),
        "tasks_path": str(tasks_path),
        "tasks_executed": len(compared),
        "matched_tasks": len(compared) - len(mismatches),
        "all_matched": bool(all_matched),
        "http_unavailable": bool(http_unavailable),
        "used_http_runtime": bool(used_http_runtime),
        "results": compared,
    }


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _run_id() -> str:
    return f"svc-parity-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
