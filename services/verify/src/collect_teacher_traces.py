from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

from services.aesa.scoring.aesa_score import score_task
from ml.resolver import resolve_callable_spec


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASKS_PATH = REPO_ROOT / "eval" / "aesa" / "tasks_mvp.jsonl"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "aesa" / "traces"
DEFAULT_BASE_MODEL = "Qwen/Qwen2.5-Coder-7B-Instruct"
ADAPTER_BUCKETS = ("coding", "writing", "mixed")


def main() -> int:
    p = argparse.ArgumentParser(description="Collect teacher traces and export verified JSONL datasets.")
    p.add_argument("--tasks", default=str(DEFAULT_TASKS_PATH), help="Input task JSONL file.")
    p.add_argument(
        "--teacher-model",
        default="ml.generator:generate_decision",
        help="Teacher callable spec (module:function).",
    )
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory for adapter datasets.")
    p.add_argument("--report", default=None, help="Optional summary report JSON path.")
    p.add_argument("--lora-plan-out", default=None, help="Optional LoRA plan JSON path.")
    p.add_argument("--max-tasks", type=int, default=None, help="Limit number of tasks processed.")
    p.add_argument(
        "--include-failed",
        action="store_true",
        help="Also export failed traces. Default exports only gate-passed traces.",
    )
    p.add_argument("--base-model", default=DEFAULT_BASE_MODEL, help="Base model for LoRA plan metadata.")
    p.add_argument(
        "--target-examples",
        type=int,
        default=1000,
        help="Target example count (used for LoRA plan readiness summary).",
    )
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = Path(args.report) if args.report else out_dir / "collect_report.json"
    lora_path = Path(args.lora_plan_out) if args.lora_plan_out else out_dir / "lora_plan.json"

    payload = run_collection(
        tasks_path=Path(args.tasks),
        teacher_model_spec=str(args.teacher_model),
        out_dir=out_dir,
        include_failed=bool(args.include_failed),
        max_tasks=args.max_tasks,
        report_path=report_path,
        lora_plan_out=lora_path,
        base_model=str(args.base_model),
        target_examples=int(args.target_examples),
    )

    print(f"[teacher-traces] wrote datasets to {out_dir}")
    print(
        "[teacher-traces] "
        f"total={payload.get('tasks_total', 0)} "
        f"passed={payload.get('tasks_passed', 0)} "
        f"exported={payload.get('tasks_exported', 0)} "
        f"goal={payload.get('target_examples', 0)} "
        f"goal_reached={payload.get('goal_reached', False)}"
    )
    return 0


def run_collection(
    *,
    tasks_path: Path,
    teacher_model_spec: str,
    out_dir: Path,
    include_failed: bool,
    max_tasks: int | None,
    report_path: Path,
    lora_plan_out: Path,
    base_model: str,
    target_examples: int,
) -> Dict[str, Any]:
    teacher_fn = resolve_callable_spec(teacher_model_spec)
    rows = _load_jsonl(tasks_path)
    if max_tasks is not None and max_tasks > 0:
        rows = rows[: int(max_tasks)]

    exported: Dict[str, List[Dict[str, Any]]] = {k: [] for k in ADAPTER_BUCKETS}
    tasks_passed = 0
    teacher_failures = 0
    fail_reasons: Counter[str] = Counter()

    for idx, row in enumerate(rows, start=1):
        trace = _collect_one(row=row, idx=idx, teacher_fn=teacher_fn, teacher_model_spec=teacher_model_spec)
        passed = bool(((trace.get("gates") or {}).get("passed")))
        if passed:
            tasks_passed += 1
        else:
            for reason in ((trace.get("gates") or {}).get("reasons") or []):
                fail_reasons[str(reason)] += 1
        if bool(trace.get("teacher_call_failed")):
            teacher_failures += 1

        if passed or include_failed:
            bucket = str(trace.get("adapter_bucket", "mixed"))
            if bucket not in exported:
                bucket = "mixed"
            exported[bucket].append(trace)

    out_files = _write_datasets(out_dir, exported)
    tasks_exported = sum(len(v) for v in exported.values())
    goal_reached = tasks_exported >= int(target_examples)

    report = {
        "run_id": _run_id(),
        "generated_at": _utc_now(),
        "tasks_path": str(tasks_path),
        "teacher_model": teacher_model_spec,
        "tasks_total": len(rows),
        "tasks_passed": tasks_passed,
        "tasks_exported": tasks_exported,
        "teacher_failures": teacher_failures,
        "include_failed": bool(include_failed),
        "out_files": out_files,
        "top_failure_reasons": [
            {"reason": reason, "count": int(count)}
            for reason, count in sorted(fail_reasons.items(), key=lambda x: (-x[1], x[0]))[:5]
        ],
        "target_examples": int(target_examples),
        "goal_reached": bool(goal_reached),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lora_plan = _build_lora_plan(
        base_model=base_model,
        out_files=out_files,
        exported=exported,
        report=report,
        target_examples=target_examples,
    )
    lora_plan_out.parent.mkdir(parents=True, exist_ok=True)
    lora_plan_out.write_text(json.dumps(lora_plan, indent=2, ensure_ascii=False), encoding="utf-8")

    return report


def _collect_one(
    *,
    row: Dict[str, Any],
    idx: int,
    teacher_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    teacher_model_spec: str,
) -> Dict[str, Any]:
    task_id = str(row.get("task_id") or f"TRACE-{idx:04d}")
    prompt = str(row.get("prompt") or row.get("title") or row.get("objective") or "").strip()
    context = _extract_context(row)
    bucket = _adapter_bucket(row)

    teacher_input = {
        "task_id": task_id,
        "category": str(row.get("category", "")),
        "adapter_bucket": bucket,
        "prompt": prompt,
        "context": context,
        "task": row,
        "required_trace_fields": ["prompt", "context", "tools", "diff", "tests"],
    }
    started = time.monotonic()
    teacher_call_failed = False
    teacher_output: Dict[str, Any]
    try:
        raw = teacher_fn(teacher_input)
        teacher_output = raw if isinstance(raw, dict) else {"raw_output": raw}
    except Exception as exc:
        teacher_call_failed = True
        teacher_output = {"error": f"teacher_call_failed:{type(exc).__name__}"}
    latency_ms = int((time.monotonic() - started) * 1000)

    tools = _extract_tools(teacher_output)
    diff = _extract_diff(teacher_output)
    tests = _extract_tests(teacher_output)
    signals = _derive_signals(row=row, teacher_output=teacher_output, tools=tools, diff=diff, tests=tests, latency_ms=latency_ms)

    task_for_score = {
        "task_id": task_id,
        "category": bucket,
    }
    scored = score_task(task=task_for_score, signals=signals)
    gates = {
        "passed": bool(scored.get("passed")),
        "reasons": list(scored.get("reasons") or []),
        "scores": dict(scored.get("scores") or {}),
        "signals": dict(scored.get("signals") or {}),
    }

    return {
        "trace_id": f"trace-{task_id}-{int(time.time() * 1000)}",
        "generated_at": _utc_now(),
        "task_id": task_id,
        "adapter_bucket": bucket,
        "prompt": prompt,
        "context": context,
        "teacher": {
            "model_spec": teacher_model_spec,
            "input": teacher_input,
            "output": teacher_output,
        },
        "teacher_call_failed": bool(teacher_call_failed),
        "tools": tools,
        "diff": diff,
        "tests": tests,
        "gates": gates,
    }


def _extract_context(row: Dict[str, Any]) -> Dict[str, Any]:
    context: Dict[str, Any] = {}
    for key in ("scope", "constraints", "policy", "metadata", "spec", "expected", "success"):
        val = row.get(key)
        if isinstance(val, (dict, list, str, int, float, bool)):
            context[key] = val
    if not context and isinstance(row.get("title"), str):
        context["title"] = row.get("title")
    return context


def _extract_tools(out: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = out.get("tools")
    items: List[Dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                items.append(dict(item))
    if not items:
        used = out.get("used_tools")
        if isinstance(used, list):
            for name in used:
                items.append({"name": str(name), "ok": True})
    return items


def _extract_diff(out: Dict[str, Any]) -> Dict[str, Any]:
    diff = out.get("diff")
    if isinstance(diff, dict):
        return dict(diff)

    patch = out.get("patch_text")
    if not isinstance(patch, str):
        patch = out.get("test_patch_text")
    if not isinstance(patch, str):
        patch = ""

    lines_added = 0
    lines_removed = 0
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines_added += 1
        elif line.startswith("-") and not line.startswith("---"):
            lines_removed += 1

    return {
        "patch_text": patch,
        "lines_added": int(lines_added),
        "lines_removed": int(lines_removed),
        "files_changed": out.get("files_changed"),
    }


def _extract_tests(out: Dict[str, Any]) -> Dict[str, Any]:
    tests = out.get("tests")
    if isinstance(tests, dict):
        return dict(tests)
    return {
        "commands": out.get("test_commands") if isinstance(out.get("test_commands"), list) else [],
        "passed": bool(out.get("tests_passed", False)),
    }


def _derive_signals(
    *,
    row: Dict[str, Any],
    teacher_output: Dict[str, Any],
    tools: List[Dict[str, Any]],
    diff: Dict[str, Any],
    tests: Dict[str, Any],
    latency_ms: int,
) -> Dict[str, Any]:
    teacher_signals = teacher_output.get("signals")
    if isinstance(teacher_signals, dict):
        out = dict(teacher_signals)
    else:
        task_signals = row.get("signals")
        out = dict(task_signals) if isinstance(task_signals, dict) else {}

    if "tests_passed" not in out:
        out["tests_passed"] = bool(tests.get("passed", False))
    if "lint_passed" not in out:
        out["lint_passed"] = bool(out.get("tests_passed", False))
    if "scope_violations" not in out:
        out["scope_violations"] = 1 if _has_scope_violation(tools) else 0
    if "timed_out" not in out:
        out["timed_out"] = bool(tests.get("timed_out", False))
    if "repairs_used" not in out:
        out["repairs_used"] = 0
    if "forbidden_paths_touched" not in out:
        out["forbidden_paths_touched"] = 0

    lines_added = _to_int(diff.get("lines_added"), default=0)
    lines_removed = _to_int(diff.get("lines_removed"), default=0)
    if "patch_size_lines" not in out:
        out["patch_size_lines"] = max(0, lines_added + lines_removed)
    if "latency_ms" not in out:
        out["latency_ms"] = max(1, int(latency_ms))
    return out


def _adapter_bucket(row: Dict[str, Any]) -> str:
    raw = str(row.get("category") or row.get("task_type") or "").strip().lower()
    task_id = str(row.get("task_id") or "").upper()

    if raw in {"code", "coding", "test", "tests"}:
        return "coding"
    if raw in {"docs", "doc", "documentation", "writing", "text"}:
        return "writing"
    if task_id.startswith("CODE-"):
        return "coding"
    return "mixed"


def _write_datasets(out_dir: Path, rows_by_bucket: Dict[str, List[Dict[str, Any]]]) -> Dict[str, str]:
    out_files: Dict[str, str] = {}
    out_dir.mkdir(parents=True, exist_ok=True)
    for bucket in ADAPTER_BUCKETS:
        path = out_dir / f"{bucket}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for row in rows_by_bucket.get(bucket, []):
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        out_files[bucket] = str(path)
    return out_files


def _build_lora_plan(
    *,
    base_model: str,
    out_files: Dict[str, str],
    exported: Dict[str, List[Dict[str, Any]]],
    report: Dict[str, Any],
    target_examples: int,
) -> Dict[str, Any]:
    total = int(report.get("tasks_exported", 0))
    return {
        "created_at": _utc_now(),
        "base_model": base_model,
        "target_examples": int(target_examples),
        "status": "ready" if total >= int(target_examples) else "collect_more",
        "adapters": [
            {
                "name": "coder",
                "dataset": out_files.get("coding"),
                "examples": len(exported.get("coding", [])),
                "lora": {"rank": 16, "alpha": 32, "dropout": 0.05},
            },
            {
                "name": "writer",
                "dataset": out_files.get("writing"),
                "examples": len(exported.get("writing", [])),
                "lora": {"rank": 8, "alpha": 16, "dropout": 0.05},
            },
            {
                "name": "mixed",
                "dataset": out_files.get("mixed"),
                "examples": len(exported.get("mixed", [])),
                "lora": {"rank": 12, "alpha": 24, "dropout": 0.05},
            },
        ],
    }


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def _to_int(value: Any, *, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _has_scope_violation(tools: List[Dict[str, Any]]) -> bool:
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        output = tool.get("output")
        if not isinstance(output, dict):
            continue
        err = str(output.get("error", "")).strip().lower()
        if err in {"scope_violation", "protected_path_violation"}:
            return True
    return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _run_id() -> str:
    return f"teacher-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


if __name__ == "__main__":
    raise SystemExit(main())
