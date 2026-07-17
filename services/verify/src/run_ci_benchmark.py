from __future__ import annotations

import argparse
import json
import os
import statistics
import uuid
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from agents.architect_agent import ArchitectAgent
from agents.failure_logger_agent import FailureLoggerAgent
from babyai_shared.bus.protocol import Context, Message, MessageType
from agents.registry import AgentRegistry
from agents.repair_agent import RepairAgent
from agents.supervisor_agent import SupervisorAgent
from agents.translator_agent import TranslatorAgent
from agents.validation_agent import ValidationAgent
from ml.judges.quality import get_judge_quality_metrics_service
from ml.observation import compute_observation
from babyai_shared.model_runtime.model_zoo import ModelZoo
from babyai_shared.storage.safe_paths import safe_segment
from babyai_shared.truth.loader import load_truth_pack
from babyai_shared.truth.integrity import compute_truth_integrity
from verify.artifact_manifest import assert_artifact_manifest_integrity, write_artifact_manifest
from verify.artifacts.registry import write_artifact
from verify.build_info import collect_build_info, write_build_info
from verify.eval_fingerprint import build_eval_manifest, compute_eval_set_fingerprint
from verify.fault_suite import extract_fault_metrics, run_fault_suite
from verify.resolved_config import build_resolved_config, write_resolved_config
from verify.run_coding_suite import run_coding_suite


REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_SCHEMA_VERSION = 1


def _resolve_callable(spec: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    import importlib

    if ":" not in spec:
        raise ValueError(f"Callable must be 'module:function', got: {spec}")
    mod_name, fn_name = spec.split(":", 1)
    mod = importlib.import_module(mod_name)
    fn = getattr(mod, fn_name, None)
    if not callable(fn):
        raise RuntimeError(f"Generator callable not found: {spec}")
    return fn  # type: ignore[return-value]


def _load_split(path: Optional[str]) -> Optional[set[str]]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Split file not found: {p}")
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        return set()
    if text.lstrip().startswith("["):
        data = json.loads(text)
        if isinstance(data, list):
            return {
                normalized
                for x in data
                for normalized in [_normalize_split_entry(str(x))]
                if normalized
            }
    return {
        normalized
        for ln in text.splitlines()
        for normalized in [_normalize_split_entry(ln)]
        if normalized
    }


def _normalize_split_entry(raw: str) -> str:
    text = str(raw).strip()
    if not text:
        return ""
    normalized = text.replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1]
    if basename.lower().endswith(".json"):
        basename = basename[:-5]
    return basename.strip()


def _task_id(task: Dict[str, Any], fallback: str) -> str:
    if isinstance(task.get("task_id"), str):
        return str(task["task_id"])
    spec = task.get("spec")
    if isinstance(spec, dict) and isinstance(spec.get("id"), str):
        return str(spec["id"])
    return fallback


def _load_tasks(tasks_dir: Path, split: Optional[set[str]] = None) -> List[Tuple[str, Dict[str, Any]]]:
    if not tasks_dir.exists():
        raise FileNotFoundError(f"Tasks dir not found: {tasks_dir}")

    tasks: List[Tuple[str, Dict[str, Any]]] = []
    for p in sorted(tasks_dir.glob("*.json")):
        obj = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise TypeError(f"Task must be dict JSON: {p}")
        tid = _task_id(obj, p.stem)
        canonical_tid = _normalize_split_entry(tid)
        if split is not None and canonical_tid not in split:
            continue
        tasks.append((str(p), obj))
    if split is not None and not tasks:
        raise ValueError("Split file matched 0 tasks. Expected one of: task_id, filename, or relative path.")
    return tasks


def _make_registry(generator_fn: Callable[[Dict[str, Any]], Dict[str, Any]], log_path: Path) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register(SupervisorAgent())
    registry.register(ArchitectAgent(generator=generator_fn))
    registry.register(ValidationAgent())
    registry.register(RepairAgent())
    registry.register(TranslatorAgent())
    registry.register(FailureLoggerAgent(log_path=str(log_path)))
    return registry


def _classify_stop_reason(message: Optional[str]) -> str:
    if not message:
        return "unknown"
    msg = message.lower()
    if "specification is incomplete" in msg or "spec is incomplete" in msg:
        return "spec_incomplete"
    if "universalplan update failed" in msg or "plandiff" in msg:
        return "plan_diff_conflict"
    if "unchanged failure reasons" in msg or "hard failure tags" in msg:
        return "no_progress"
    if "flipped ops gate" in msg:
        return "gate_flipped_but_still_failed"
    if "not repairable" in msg:
        return "unrecoverable"
    if "validation failed after" in msg:
        return "budget_exhausted"
    if "repeating" in msg:
        return "repeating_error"
    if msg.startswith("repair failed"):
        return "repair_failed"
    if "max_steps" in msg or "max steps" in msg:
        return "max_steps"
    return "other"


def _extract_failure_codes(context: Context, user_msg: Optional[Message]) -> List[str]:
    result = context.validation_results
    if isinstance(result, dict):
        errors = result.get("errors", []) or []
        codes = [str(e.get("code")) for e in errors if isinstance(e, dict) and e.get("code")]
        if codes:
            return codes

    if user_msg and isinstance(user_msg.payload, dict):
        details = user_msg.payload.get("details")
        if isinstance(details, dict):
            errors = details.get("errors", []) or []
            codes = [str(e.get("code")) for e in errors if isinstance(e, dict) and e.get("code")]
            if codes:
                return codes

    return []


def _run_task(
    task: Dict[str, Any],
    *,
    registry: AgentRegistry,
    max_steps: int,
    max_repairs: int,
    truth_pack: Dict[str, Any],
    purpose: str,
) -> Tuple[Context, Optional[Message], bool]:
    context = Context(
        context_id=str(uuid.uuid4()),
        task_spec=task,
        repair_budget=max_repairs,
    )
    try:
        zoo = ModelZoo()
        selected, trace = zoo.select_runner_with_trace(truth_pack, purpose=purpose)
        context.selected_runner = selected
        context.selection_trace = trace
    except Exception:
        context.selected_runner = None
        context.selection_trace = None

    msg = Message(
        message_id=str(uuid.uuid4()),
        from_agent="benchmark",
        to_agent="supervisor-001",
        message_type=MessageType.REQUIREMENTS_COMPLETE,
        payload={"task_id": task.get("task_id")},
        context_id=context.context_id,
        timestamp=datetime.now().isoformat(),
    )

    queue: List[Message] = [msg]
    user_msg: Optional[Message] = None
    success = False

    steps = 0
    while queue and steps < max_steps:
        current = queue.pop(0)
        steps += 1

        if current.to_agent == "user":
            user_msg = current
            if current.message_type == MessageType.TRANSLATION_COMPLETE:
                success = True
            continue

        agent = registry.get(current.to_agent)
        if not agent:
            continue
        if not agent.can_handle(current.message_type):
            continue

        new_messages = agent.process(current, context)
        for out_msg in new_messages:
            if out_msg.message_type == MessageType.LOG_SUCCESS:
                success = True
            queue.append(out_msg)

    if steps >= max_steps:
        user_msg = Message(
            message_id=str(uuid.uuid4()),
            from_agent="benchmark",
            to_agent="user",
            message_type=MessageType.ARCHITECTURE_VALIDATION_FAILED,
            payload={"error": "Benchmark loop exceeded max_steps"},
            context_id=context.context_id,
            timestamp=datetime.now().isoformat(),
        )
        success = False

    return context, user_msg, success


def _run_id(ci_mode: bool) -> str:
    ts = datetime.now(timezone.utc).replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
    prefix = "ci" if ci_mode else "local"
    return f"{prefix}-{ts}"


def _git_info() -> Dict[str, str]:
    sha = os.getenv("GITHUB_SHA") or os.getenv("CI_COMMIT_SHA") or "unknown"
    branch = (
        os.getenv("GITHUB_REF_NAME")
        or os.getenv("GITHUB_REF")
        or os.getenv("CI_COMMIT_REF_NAME")
        or "unknown"
    )
    if sha and sha != "unknown":
        sha = sha[:7]
    return {"sha": sha, "branch": branch}


def _build_metrics(
    *,
    total: int,
    success_no_repair: int,
    success_with_repair: int,
    repairs_used: List[int],
    gate_flips: int,
    gate_total: int,
    failure_reasons: Counter,
    hard_fail_tasks: int,
    hard_fail_tags: Counter,
    wasted_repairs: int,
    fixed_by_1: int,
    no_progress_stops: int,
    stop_reasons: Counter,
    capacity_gaps: List[float],
    overload_count: int,
    runner_stats: Dict[str, Dict[str, Any]],
    latency_p50_s: float,
    latency_p95_s: float,
    runner_selection_trace: Dict[str, Any],
) -> Dict[str, Any]:
    pass_rate_no_repair = (success_no_repair / total) if total else 0.0
    pass_rate_with_repair = (success_with_repair / total) if total else 0.0
    avg_repairs = statistics.mean(repairs_used) if repairs_used else 0.0
    median_repairs = statistics.median(repairs_used) if repairs_used else 0.0
    gate_flip_rate = (gate_flips / gate_total) if gate_total else 0.0
    capacity_gap_avg = statistics.mean(capacity_gaps) if capacity_gaps else 0.0
    overload_rate = (overload_count / total) if total else 0.0

    top_failure_reasons = [
        {"reason": k, "count": v}
        for k, v in sorted(failure_reasons.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    ]
    top_failures = [{"tag": str(item["reason"]), "count": int(item["count"])} for item in top_failure_reasons]
    hard_fail_rate = (hard_fail_tasks / total) if total else 0.0
    top_hard_fail_tags = [
        {"tag": k, "count": v}
        for k, v in sorted(hard_fail_tags.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
    ]

    stop_reason_counts = [
        {"stop_reason": k, "count": v}
        for k, v in sorted(stop_reasons.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    runner_metrics = []
    for runner, stats in sorted(runner_stats.items(), key=lambda kv: kv[0]):
        total_r = stats.get("total", 0)
        success_r = stats.get("success", 0)
        latency_sum = stats.get("latency_sum", 0.0)
        tokens_sum = stats.get("tokens_sum")
        tokens_count = stats.get("tokens_count", 0)
        avg_latency = (latency_sum / total_r) if total_r else 0.0
        avg_tokens = (tokens_sum / tokens_count) if tokens_sum is not None and tokens_count else None
        runner_metrics.append({
            "runner": runner,
            "success_rate": round((success_r / total_r) if total_r else 0.0, 3),
            "avg_latency_ms": round(avg_latency, 2),
            "avg_tokens": round(avg_tokens, 2) if isinstance(avg_tokens, (int, float)) else None,
            "count": int(total_r),
        })

    return {
        "pass_rate_no_repair": round(pass_rate_no_repair, 3),
        "pass_rate_with_repair": round(pass_rate_with_repair, 3),
        "pass_rate": round(pass_rate_with_repair, 3),
        "avg_repairs_used": round(avg_repairs, 3),
        "avg_repair_steps": round(avg_repairs, 3),
        "median_repairs_used": round(median_repairs, 3),
        "gate_flip_rate": round(gate_flip_rate, 3),
        "overload_rate": round(overload_rate, 3),
        "capacity_gap_avg": round(capacity_gap_avg, 3),
        "latency_p50_s": round(latency_p50_s, 3),
        "latency_p95_s": round(latency_p95_s, 3),
        "top_failure_reasons": top_failure_reasons,
        "top_failures": top_failures,
        "hard_fail_rate": round(hard_fail_rate, 3),
        "top_hard_fail_tags": top_hard_fail_tags,
        "wasted_repairs": int(wasted_repairs),
        "fixed_by_1": int(fixed_by_1),
        "no_progress_stops": int(no_progress_stops),
        "stop_reason_counts": stop_reason_counts,
        "runner_metrics": runner_metrics,
        "runner_selection_trace": runner_selection_trace,
    }


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    k = (pct / 100.0) * (len(data) - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(data[int(k)])
    d0 = data[int(f)] * (c - k)
    d1 = data[int(c)] * (k - f)
    return float(d0 + d1)


def _build_scoreline(metrics: Dict[str, Any]) -> Dict[str, Any]:
    coding = metrics.get("coding_suite", {}) if isinstance(metrics, dict) else {}
    judge_quality = metrics.get("judge_quality", {}) if isinstance(metrics.get("judge_quality"), dict) else {}
    pass_no_repair = float(metrics.get("pass_rate_no_repair", 0.0) or 0.0)
    pass_with_repair = float(metrics.get("pass_rate_with_repair", 0.0) or 0.0)
    avg_repair_steps = float(metrics.get("avg_repair_steps", metrics.get("avg_repairs_used", 0.0)) or 0.0)
    median_repairs = float(metrics.get("median_repairs_used", 0.0) or 0.0)
    gate_flip_rate = float(metrics.get("gate_flip_rate", 0.0) or 0.0)
    latency_p50_s = float(metrics.get("latency_p50_s", 0.0) or 0.0)
    latency_p95_s = float(metrics.get("latency_p95_s", 0.0) or 0.0)
    judge_agreement = float(judge_quality.get("agreement", 0.0) or 0.0)
    judge_precision = float(judge_quality.get("precision", 0.0) or 0.0)
    judge_recall = float(judge_quality.get("recall", 0.0) or 0.0)
    top_failures = metrics.get("top_failures", metrics.get("top_failure_reasons", []))
    return {
        "pass_rate": pass_with_repair,
        "pass_no_repair": pass_no_repair,
        "pass_with_repair": pass_with_repair,
        "pass_rate_detail": {
            "no_repair": pass_no_repair,
            "with_repair": pass_with_repair,
        },
        "avg_repair_steps": avg_repair_steps,
        "median_repair_steps": median_repairs,
        "repair_effectiveness": {
            "lift": round(pass_with_repair - pass_no_repair, 3),
            "avg_steps": avg_repair_steps,
            "median_steps": median_repairs,
        },
        "gate_flip_rate": gate_flip_rate,
        "latency_p50_s": latency_p50_s,
        "latency_p95_s": latency_p95_s,
        "latency": {
            "p50_s": latency_p50_s,
            "p95_s": latency_p95_s,
        },
        "top_failure_reasons": metrics.get("top_failure_reasons", []),
        "top_failures": top_failures,
        "hard_fail_rate": float(metrics.get("hard_fail_rate", 0.0) or 0.0),
        "top_hard_fail_tags": metrics.get("top_hard_fail_tags", []),
        "wasted_repairs": int(metrics.get("wasted_repairs", 0) or 0),
        "fixed_by_1": int(metrics.get("fixed_by_1", 0) or 0),
        "no_progress_stops": int(metrics.get("no_progress_stops", 0) or 0),
        "runner_selection_trace": metrics.get("runner_selection_trace", {}),
        "coding_pass_rate": coding.get("pass_rate", 0.0),
        "coding_avg_repairs": coding.get("avg_repairs_used", 0.0),
        "coding_scope_violation_rate": coding.get("scope_violation_rate", 0.0),
        "judge_agreement": judge_agreement,
        "judge_precision": judge_precision,
        "judge_recall": judge_recall,
        "fail_closed_rate": float(metrics.get("fail_closed_rate", 0.0) or 0.0),
        "fault_containment_pass_rate": float(metrics.get("fault_containment_pass_rate", 0.0) or 0.0),
        "mean_repair_steps": float(metrics.get("mean_repair_steps", 0.0) or 0.0),
        "eval_set_fingerprint": str(metrics.get("eval_set_fingerprint", "")),
        "config_fingerprint": str(metrics.get("config_fingerprint", "")),
    }


def _format_scoreline(scoreline: Dict[str, Any]) -> str:
    top = scoreline.get("top_failures") or scoreline.get("top_failure_reasons") or []
    top_reason = "none"
    top_count = 0
    if top:
        top_reason = str(top[0].get("tag", top[0].get("reason", "none")))
        top_count = int(top[0].get("count", 0) or 0)
    return (
        f"PASS={float(scoreline.get('pass_rate', 0.0)):.3f} "
        f"NO_REPAIR={float(scoreline.get('pass_no_repair', 0.0)):.3f} "
        f"WITH_REPAIR={float(scoreline.get('pass_with_repair', 0.0)):.3f} "
        f"AVG_REPAIR={float(scoreline.get('avg_repair_steps', 0.0)):.3f} "
        f"HARD_FAIL={float(scoreline.get('hard_fail_rate', 0.0)):.3f} "
        f"GATE={float(scoreline.get('gate_flip_rate', 0.0)):.3f} "
        f"FAIL_CLOSED={float(scoreline.get('fail_closed_rate', 0.0)):.3f} "
        f"FAULT_CONTAIN={float(scoreline.get('fault_containment_pass_rate', 0.0)):.3f} "
        f"FAULT_REPAIR={float(scoreline.get('mean_repair_steps', 0.0)):.3f} "
        f"LAT_P50={float(scoreline.get('latency_p50_s', 0.0)):.2f}s "
        f"LAT_P95={float(scoreline.get('latency_p95_s', 0.0)):.2f}s "
        f"TOP={top_reason}:{top_count}"
    )


def _parse_scoreline(line: str) -> Dict[str, str]:
    parts = [p for p in line.strip().split() if p]
    parsed: Dict[str, str] = {}
    for part in parts:
        if "=" not in part:
            raise ValueError(f"Malformed token: {part}")
        key, value = part.split("=", 1)
        if not key or not value:
            raise ValueError(f"Malformed token: {part}")
        parsed[key] = value
    required = {"PASS", "NO_REPAIR", "WITH_REPAIR", "AVG_REPAIR", "HARD_FAIL", "GATE", "LAT_P50", "LAT_P95", "TOP"}
    missing = required - set(parsed.keys())
    if missing:
        raise ValueError(f"Missing keys in scoreline: {sorted(missing)}")
    return parsed


def _artifact_paths(*, out_path: Path, artifact_dir: Path, run_id: str | None = None) -> Dict[str, str]:
    root = Path(artifact_dir)
    scorecard_path = Path(out_path)
    run_dir = root / safe_segment(run_id or "latest")
    meta_dir = root.parent / "meta"
    compat_latest = root / "latest_scoreline.json"
    return {
        "scorecard": scorecard_path.as_posix(),
        "latest_scoreline_json": scorecard_path.as_posix(),
        "compat_latest_scoreline_json": compat_latest.as_posix(),
        "benchmark_latest_json": (root / "benchmark_latest.json").as_posix(),
        "benchmark_latest_md": (root / "benchmark_latest.md").as_posix(),
        "fault_suite_json": (root / "fault_suite_latest.json").as_posix(),
        "per_task_jsonl": (root / "per_task_latest.jsonl").as_posix(),
        "judge_quality_json": (root / "judge_quality_latest.json").as_posix(),
        "eval_manifest_json": (root / "eval_set_manifest.json").as_posix(),
        "resolved_config_json": (meta_dir / "resolved_config.json").as_posix(),
        "build_info_json": (meta_dir / "build_info.json").as_posix(),
        "manifest_json": (root / "manifest.json").as_posix(),
        "run_manifest_json": (run_dir / "manifest.json").as_posix(),
    }


def _atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    _ = encoding
    write_artifact(
        _artifact_type_for_path(path, default="benchmark_text"),
        text,
        path,
        metadata={"source_ref": "verify.run_ci_benchmark"},
    )


def _atomic_write_json(path: Path, payload: Dict[str, Any], *, indent: int | None, ensure_ascii: bool) -> None:
    text = json.dumps(payload, indent=indent, ensure_ascii=ensure_ascii)
    _atomic_write_text(path, text, encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    lines: List[str] = []
    for row in rows:
        lines.append(json.dumps(row, ensure_ascii=True, sort_keys=True))
    payload = ("\n".join(lines) + "\n") if lines else ""
    _atomic_write_text(path, payload, encoding="utf-8")


def _artifact_type_for_path(path: Path, *, default: str) -> str:
    name = path.name.lower()
    if name.endswith(".jsonl"):
        return "benchmark_jsonl"
    if name.endswith(".md"):
        return "benchmark_markdown"
    if name.endswith(".json"):
        return "benchmark_json"
    return default


def _render_benchmark_markdown(scorecard: Dict[str, Any]) -> str:
    scoreline = scorecard.get("scoreline", {}) if isinstance(scorecard, dict) else {}
    top = scoreline.get("top_failures") or scoreline.get("top_failure_reasons") or []
    lines = [
        "# Benchmark Latest",
        "",
        f"- Schema Version: {scorecard.get('schema_version')}",
        f"- Run: {scorecard.get('run_id')}",
        f"- Created At: {scorecard.get('created_at_utc')}",
        f"- SHA: {(scorecard.get('git') or {}).get('sha')}",
        f"- CONFIG_FINGERPRINT: {scorecard.get('config_fingerprint')}",
        f"- PASS_NO_REPAIR: {scoreline.get('pass_no_repair')}",
        f"- PASS_WITH_REPAIR: {scoreline.get('pass_with_repair')}",
        f"- AVG_REPAIR_STEPS: {scoreline.get('avg_repair_steps')}",
        f"- HARD_FAIL_RATE: {scoreline.get('hard_fail_rate')}",
        f"- WASTED_REPAIRS: {scoreline.get('wasted_repairs')}",
        f"- FIXED_BY_1: {scoreline.get('fixed_by_1')}",
        f"- NO_PROGRESS_STOPS: {scoreline.get('no_progress_stops')}",
        f"- FAIL_CLOSED_RATE: {scoreline.get('fail_closed_rate')}",
        f"- FAULT_CONTAINMENT_PASS_RATE: {scoreline.get('fault_containment_pass_rate')}",
        f"- FAULT_MEAN_REPAIR_STEPS: {scoreline.get('mean_repair_steps')}",
        f"- GATE_FLIP_RATE: {scoreline.get('gate_flip_rate')}",
        f"- LATENCY_P50_S: {scoreline.get('latency_p50_s')}",
        f"- LATENCY_P95_S: {scoreline.get('latency_p95_s')}",
        f"- JUDGE_AGREEMENT: {scoreline.get('judge_agreement')}",
        f"- JUDGE_PRECISION: {scoreline.get('judge_precision')}",
        f"- JUDGE_RECALL: {scoreline.get('judge_recall')}",
    ]
    if isinstance(top, list) and top:
        lines.append("- TOP_FAILURES:")
        for item in top:
            tag = str(item.get("tag", item.get("reason", "unknown")))
            count = int(item.get("count", 0) or 0)
            lines.append(f"  - {tag}: {count}")
    hard_top = scoreline.get("top_hard_fail_tags", [])
    if isinstance(hard_top, list) and hard_top:
        lines.append("- TOP_HARD_FAIL_TAGS:")
        for item in hard_top:
            tag = str(item.get("tag", "unknown"))
            count = int(item.get("count", 0) or 0)
            lines.append(f"  - {tag}: {count}")
    return "\n".join(lines).rstrip() + "\n"


def _write_benchmark_artifacts(
    *,
    scorecard: Dict[str, Any],
    artifact_paths: Dict[str, str],
    resolved_config: Dict[str, Any] | None = None,
    build_info: Dict[str, Any] | None = None,
) -> None:
    latest_scorecard_path = Path(str(artifact_paths["latest_scoreline_json"]))
    compat_latest_scorecard_path = Path(str(artifact_paths["compat_latest_scoreline_json"]))
    benchmark_json_path = Path(str(artifact_paths["benchmark_latest_json"]))
    benchmark_md_path = Path(str(artifact_paths["benchmark_latest_md"]))
    fault_suite_json_path = Path(str(artifact_paths["fault_suite_json"]))
    per_task_jsonl_path = Path(str(artifact_paths["per_task_jsonl"]))
    judge_quality_json_path = Path(str(artifact_paths["judge_quality_json"]))
    eval_manifest_json_path = Path(str(artifact_paths["eval_manifest_json"]))
    resolved_config_json_path = Path(str(artifact_paths["resolved_config_json"]))
    build_info_json_path = Path(str(artifact_paths["build_info_json"]))
    manifest_path = Path(str(artifact_paths["manifest_json"]))
    run_manifest_path = Path(str(artifact_paths["run_manifest_json"]))

    json_targets: List[Path] = []
    for candidate in [latest_scorecard_path, compat_latest_scorecard_path, benchmark_json_path]:
        if candidate not in json_targets:
            json_targets.append(candidate)
    for target in json_targets:
        _atomic_write_json(target, scorecard, indent=2, ensure_ascii=False)

    _atomic_write_text(benchmark_md_path, _render_benchmark_markdown(scorecard), encoding="utf-8")

    rows = scorecard.get("per_task_breakdown")
    if isinstance(rows, list):
        normalized_rows = [row for row in rows if isinstance(row, dict)]
    else:
        normalized_rows = []
    _write_jsonl(per_task_jsonl_path, normalized_rows)
    fault_suite_payload = (scorecard.get("metrics") or {}).get("fault_suite")
    if isinstance(fault_suite_payload, dict):
        _atomic_write_json(fault_suite_json_path, fault_suite_payload, indent=2, ensure_ascii=False)
    judge_quality_payload = (scorecard.get("metrics") or {}).get("judge_quality")
    if isinstance(judge_quality_payload, dict):
        _atomic_write_json(judge_quality_json_path, judge_quality_payload, indent=2, ensure_ascii=False)

    eval_manifest_payload = scorecard.get("eval_set_manifest")
    if isinstance(eval_manifest_payload, dict):
        _atomic_write_json(eval_manifest_json_path, eval_manifest_payload, indent=2, ensure_ascii=False)
    if isinstance(resolved_config, dict):
        write_resolved_config(resolved_config_json_path, resolved_config)
    if isinstance(build_info, dict):
        write_build_info(build_info_json_path, build_info)

    manifest_paths = dict(artifact_paths)
    if not judge_quality_json_path.exists():
        manifest_paths.pop("judge_quality_json", None)
    if not fault_suite_json_path.exists():
        manifest_paths.pop("fault_suite_json", None)
    if not eval_manifest_json_path.exists():
        manifest_paths.pop("eval_manifest_json", None)
    if not resolved_config_json_path.exists():
        manifest_paths.pop("resolved_config_json", None)
    if not build_info_json_path.exists():
        manifest_paths.pop("build_info_json", None)

    manifest = write_artifact_manifest(
        output_path=run_manifest_path,
        run_id=str(scorecard.get("run_id") or ""),
        artifact_paths=manifest_paths,
        created_at_utc=str(scorecard.get("created_at_utc") or ""),
        schema_version=int(BENCHMARK_SCHEMA_VERSION),
    )
    # Keep a compatibility pointer for existing readers.
    _atomic_write_json(manifest_path, manifest, indent=2, ensure_ascii=False)
    assert_artifact_manifest_integrity(run_manifest_path)
    assert_artifact_manifest_integrity(manifest_path)


def _compare_with_baseline(
    current: Dict[str, Any],
    baseline_path: str,
    *,
    max_drop_pass_rate: float,
    max_increase_avg_repairs: float,
) -> Tuple[bool, List[str]]:
    try:
        baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except Exception as e:
        return True, [f"compare_warn: could not load baseline: {e}"]

    cur = current.get("metrics", {}) if isinstance(current, dict) else {}
    base = baseline.get("metrics", {}) if isinstance(baseline, dict) else {}

    reasons: List[str] = []
    ok = True

    try:
        cur_pr = float(cur.get("pass_rate_with_repair", 0.0))
        base_pr = float(base.get("pass_rate_with_repair", 0.0))
        drop = base_pr - cur_pr
        if drop > max_drop_pass_rate:
            ok = False
            reasons.append(
                f"pass_rate_with_repair dropped by {drop:.3f} (baseline={base_pr:.3f}, current={cur_pr:.3f})"
            )
    except Exception:
        pass

    try:
        cur_avg = float(cur.get("avg_repairs_used", 0.0))
        base_avg = float(base.get("avg_repairs_used", 0.0))
        inc = cur_avg - base_avg
        if inc > max_increase_avg_repairs:
            ok = False
            reasons.append(
                f"avg_repairs_used increased by {inc:.3f} (baseline={base_avg:.3f}, current={cur_avg:.3f})"
            )
    except Exception:
        pass

    return ok, reasons


def _run_coding_suite(
    *,
    tasks_path: Path,
    max_tasks: Optional[int],
    run_id: str,
    out_path: Path,
    truth_pack: str = "default",
    promote_truth: bool = False,
    no_librarians: bool = False,
) -> Dict[str, Any]:
    raw_run_id = str(run_id)
    path_safe_run_id = safe_segment(raw_run_id)
    payload = run_coding_suite(
        tasks_path=tasks_path,
        truth_pack=truth_pack,
        max_tasks=max_tasks,
        promote_truth=promote_truth,
        snapshot_commit="HEAD",
        librarian_registry="knowledge/registry.sqlite",
        librarian_artifact_root="artifacts",
        no_librarians=no_librarians,
        max_librarian_hits=5,
        run_id=path_safe_run_id,
    )
    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.setdefault("raw_run_id", raw_run_id)
    metadata.setdefault("path_safe_run_id", path_safe_run_id)
    payload["metadata"] = metadata
    write_artifact(
        "coding_suite_json",
        payload,
        out_path,
        metadata={"source_ref": "verify.run_ci_benchmark._run_coding_suite"},
    )
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description="Run CI benchmark scorecard.")
    p.add_argument("--tasks-dir", default="eval/tasks", help="Directory with eval task JSON files.")
    p.add_argument("--split", default=None, help="Optional file containing task_ids to include.")
    p.add_argument(
        "--generator",
        default="ml.hybrid_generator:generate_decision",
        help="Callable to use, format 'module:function'.",
    )
    p.add_argument(
        "--out",
        default="artifacts/benchmark/latest_scoreline.json",
        help="Output JSON path.",
    )
    p.add_argument(
        "--artifact-dir",
        default="artifacts/benchmark",
        help="Directory for standard benchmark artifacts (json/jsonl/md).",
    )
    p.add_argument("--compare-to", default=None, help="Optional baseline JSON to compare against.")
    p.add_argument("--max-steps", type=int, default=200, help="Max message processing steps per task.")
    p.add_argument("--max-repairs", type=int, default=2, help="Max repair attempts per task.")
    p.add_argument("--log-path", default="logs/benchmark_failures.jsonl", help="FailureLogger output path.")
    p.add_argument("--append-log", action="store_true", help="Append to log file instead of truncating.")
    p.add_argument("--coding-tasks", default="eval/coding/tasks_mvp.jsonl", help="Coding tasks JSONL path.")
    p.add_argument("--coding-max-tasks", type=int, default=None, help="Limit coding tasks executed.")
    p.add_argument(
        "--coding-out",
        default="artifacts/coding_suite/latest.json",
        help="Coding suite output JSON path.",
    )
    p.add_argument("--no-coding-suite", action="store_true", help="Disable coding suite.")
    p.add_argument("--coding-no-librarians", action="store_true", help="Disable librarians for coding suite.")
    p.add_argument("--no-fault-suite", action="store_true", help="Disable deterministic fault-injection suite.")
    p.add_argument(
        "--fault-max-cases",
        type=int,
        default=18,
        help="Fault suite case count (recommended 10-30).",
    )
    p.add_argument(
        "--fault-max-repair-steps",
        type=int,
        default=3,
        help="Max repair steps per fault case before forced stop.",
    )
    p.add_argument(
        "--fault-soft-pass-threshold",
        type=float,
        default=0.85,
        help="Soft score threshold used by fault-suite pass logic.",
    )
    p.add_argument(
        "--judge-gold-set",
        default="eval/judges/gold_set.json",
        help="Judge gold-set fixture file for quality metrics.",
    )
    p.add_argument("--no-judge-quality", action="store_true", help="Disable judge quality metrics.")
    p.add_argument(
        "--print-json-line",
        dest="print_json_line",
        action="store_true",
        default=True,
        help="Print one-line JSON scorecard (default).",
    )
    p.add_argument(
        "--no-print-json-line",
        dest="print_json_line",
        action="store_false",
        help="Disable one-line JSON scorecard output.",
    )
    p.add_argument(
        "--print-scoreline",
        dest="print_scoreline",
        action="store_true",
        default=True,
        help="Print parseable scoreline (default).",
    )
    p.add_argument(
        "--no-print-scoreline",
        dest="print_scoreline",
        action="store_false",
        help="Disable parseable scoreline output.",
    )
    p.add_argument(
        "--max-drop-pass-rate",
        type=float,
        default=0.01,
        help="Fail if pass_rate_with_repair drops more than this vs baseline.",
    )
    p.add_argument(
        "--max-increase-avg-repairs",
        type=float,
        default=0.10,
        help="Fail if avg_repairs_used increases more than this vs baseline.",
    )
    args = p.parse_args()

    split = _load_split(args.split) if args.split else None
    run_id = _run_id(ci_mode=bool(os.getenv("CI")))
    from policy.constitution_service import get_constitution_service
    constitution = get_constitution_service()

    tasks_dir = Path(args.tasks_dir)
    log_path = Path(args.log_path)
    constitution.require("write_path", {"path": log_path, "operation": "benchmark_log_init"})
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not args.append_log:
        log_path.write_text("", encoding="utf-8")
    elif not log_path.exists():
        log_path.touch()

    generator_fn = _resolve_callable(args.generator)
    tasks = _load_tasks(tasks_dir, split=split)
    eval_set_manifest = build_eval_manifest(tasks, root=tasks_dir)
    eval_set_fingerprint = compute_eval_set_fingerprint(eval_set_manifest)
    truth_pack = load_truth_pack()
    purpose = "benchmark"

    total = len(tasks)
    success_no_repair = 0
    success_with_repair = 0
    repairs_used: List[int] = []
    gate_flips = 0
    gate_total = 0
    failure_reasons: Counter = Counter()
    hard_fail_tasks = 0
    hard_fail_tags: Counter = Counter()
    wasted_repairs = 0
    fixed_by_1 = 0
    no_progress_stops = 0
    stop_reasons: Counter = Counter()
    capacity_gaps: List[float] = []
    overload_count = 0
    per_task_breakdown: List[Dict[str, Any]] = []
    runner_stats: Dict[str, Dict[str, Any]] = {}
    latencies_ms: List[float] = []
    selection_selected: Counter = Counter()
    selection_reasons: Counter = Counter()
    selection_preferred: Counter = Counter()

    for _, task in tasks:
        runner_id = os.getenv("RUNNER_ID") or os.getenv("GENERATOR_ID") or args.generator
        start_time = datetime.now(timezone.utc)
        registry = _make_registry(generator_fn, log_path)
        context, user_msg, success = _run_task(
            task,
            registry=registry,
            max_steps=args.max_steps,
            max_repairs=args.max_repairs,
            truth_pack=truth_pack,
            purpose=purpose,
        )
        end_time = datetime.now(timezone.utc)
        latency_ms = (end_time - start_time).total_seconds() * 1000.0
        latencies_ms.append(latency_ms)

        repairs_used.append(int(context.repair_attempts))
        task_id = _task_id(task, "unknown")

        observation = None
        if isinstance(context.architecture_decision, dict):
            observation = compute_observation(task, context.architecture_decision)
            gap = observation.get("capacity_gap_hours_per_week") if isinstance(observation, dict) else None
            if isinstance(gap, (int, float)):
                capacity_gaps.append(float(gap))
                if float(gap) > 0:
                    overload_count += 1

        task_gate_flips = 0
        task_gate_total = 0
        for entry in context.repair_history:
            flip_value = entry.get("hard_gate_flip")
            if flip_value is None:
                flip_value = entry.get("gate_flip")
            if flip_value is not None:
                task_gate_total += 1
                gate_total += 1
                if flip_value is True:
                    task_gate_flips += 1
                    gate_flips += 1
                else:
                    wasted_repairs += 1

        final_failure_codes: List[str] = []
        stop_reason = "success"
        task_hard_fail_tags: List[str] = []
        gate_snapshots: List[Dict[str, Any]] = []
        for entry in context.repair_history:
            before = ((entry.get("gates_before", {}) or {}).get("hard_fail_tags", [])) if isinstance(entry, dict) else []
            after = ((entry.get("gates_after", {}) or {}).get("hard_fail_tags", [])) if isinstance(entry, dict) else []
            gate_snapshots.append(
                {
                    "attempt": int(entry.get("attempt", 0)) if isinstance(entry, dict) else 0,
                    "before": {"hard_fail_tags": [str(x) for x in before if str(x).strip()] if isinstance(before, list) else []},
                    "after": {"hard_fail_tags": [str(x) for x in after if str(x).strip()] if isinstance(after, list) else []},
                    "hard_gate_flip": entry.get("hard_gate_flip") if isinstance(entry, dict) else None,
                }
            )
        if success:
            success_with_repair += 1
            if context.repair_attempts == 0:
                success_no_repair += 1
            if context.repair_attempts == 1:
                fixed_by_1 += 1
        else:
            final_failure_codes = _extract_failure_codes(context, user_msg)
            for code in final_failure_codes or ["unknown"]:
                failure_reasons[code] += 1
            stop_reason = _classify_stop_reason(
                (user_msg.payload.get("error") if user_msg and isinstance(user_msg.payload, dict) else None)
            )
            stop_reasons[stop_reason] += 1
            if stop_reason == "no_progress":
                no_progress_stops += 1
            task_hard_fail_tags = [str(code) for code in (final_failure_codes or [stop_reason or "unknown_hard_fail"])]
            hard_fail_tasks += 1
            for tag in task_hard_fail_tags:
                hard_fail_tags[str(tag)] += 1

        per_task_breakdown.append({
            "task_id": task_id,
            "passed": success,
            "repairs_used": int(context.repair_attempts),
            "gate_flip_count": task_gate_flips,
            "gate_flip_total": task_gate_total,
            "stop_reason": stop_reason,
            "final_failure_reasons": final_failure_codes,
            "hard_fail_tags": task_hard_fail_tags,
            "gate_snapshots": gate_snapshots,
            "runner_used": context.selected_runner or runner_id,
            "latency_ms": round(latency_ms, 2),
            "eval_set_fingerprint": eval_set_fingerprint,
        })

        trace = context.selection_trace or {}
        if isinstance(trace, dict):
            selected = trace.get("selected")
            reason = trace.get("reason")
            preferred = trace.get("preferred")
            if selected:
                selection_selected[str(selected)] += 1
            if reason:
                selection_reasons[str(reason)] += 1
            if preferred:
                selection_preferred[str(preferred)] += 1

        runner_key = context.selected_runner or runner_id
        stats = runner_stats.setdefault(runner_key, {"total": 0, "success": 0, "latency_sum": 0.0, "tokens_sum": None, "tokens_count": 0})
        stats["total"] += 1
        if success:
            stats["success"] += 1
        stats["latency_sum"] += latency_ms

    latency_p50_s = _percentile(latencies_ms, 50.0) / 1000.0
    latency_p95_s = _percentile(latencies_ms, 95.0) / 1000.0

    runner_selection_trace = {
        "selected_counts": [
            {"runner": k, "count": v}
            for k, v in sorted(selection_selected.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "reason_counts": [
            {"reason": k, "count": v}
            for k, v in sorted(selection_reasons.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
        "preferred_counts": [
            {"runner": k, "count": v}
            for k, v in sorted(selection_preferred.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }

    metrics = _build_metrics(
        total=total,
        success_no_repair=success_no_repair,
        success_with_repair=success_with_repair,
        repairs_used=repairs_used,
        gate_flips=gate_flips,
        gate_total=gate_total,
        failure_reasons=failure_reasons,
        hard_fail_tasks=hard_fail_tasks,
        hard_fail_tags=hard_fail_tags,
        wasted_repairs=wasted_repairs,
        fixed_by_1=fixed_by_1,
        no_progress_stops=no_progress_stops,
        stop_reasons=stop_reasons,
        capacity_gaps=capacity_gaps,
        overload_count=overload_count,
        runner_stats=runner_stats,
        latency_p50_s=latency_p50_s,
        latency_p95_s=latency_p95_s,
        runner_selection_trace=runner_selection_trace,
    )
    metrics["eval_set_fingerprint"] = eval_set_fingerprint
    metrics["truth_integrity"] = compute_truth_integrity()
    metrics["constitution_fingerprint"] = constitution.state.fingerprint
    metrics["constitution_version"] = constitution.state.version

    if not args.no_coding_suite:
        coding_payload = _run_coding_suite(
            tasks_path=Path(args.coding_tasks),
            max_tasks=args.coding_max_tasks,
            run_id=f"{run_id}-coding",
            out_path=Path(args.coding_out),
            promote_truth=False,
            no_librarians=bool(args.coding_no_librarians),
        )
        metrics["coding_suite"] = coding_payload.get("metrics", {})
    else:
        metrics["coding_suite"] = {
            "pass_rate": 0.0,
            "avg_repairs_used": 0.0,
            "scope_violation_rate": 0.0,
            "tasks_executed": 0,
            "tasks_total": 0,
        }

    if not args.no_judge_quality:
        try:
            judge_quality = get_judge_quality_metrics_service(path=args.judge_gold_set, reload=True).evaluate().to_dict()
        except Exception as exc:
            judge_quality = {
                "schema_version": 1,
                "version": "v1",
                "gold_set_path": str(args.judge_gold_set),
                "error": f"{type(exc).__name__}: {exc}",
                "enabled": False,
                "agreement": 0.0,
                "precision": 0.0,
                "recall": 0.0,
                "total": 0,
            }
    else:
        judge_quality = {
            "schema_version": 1,
            "version": "v1",
            "gold_set_path": str(args.judge_gold_set),
            "enabled": False,
            "error": "disabled_by_flag",
            "agreement": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "total": 0,
        }
    metrics["judge_quality"] = judge_quality

    if not args.no_fault_suite:
        fault_suite_report = run_fault_suite(
            run_id=f"{run_id}-fault",
            max_cases=max(1, int(args.fault_max_cases)),
            max_repair_steps=max(1, int(args.fault_max_repair_steps)),
            soft_pass_threshold=float(args.fault_soft_pass_threshold),
            service_policies={
                "context_plane": str(os.getenv("CONTEXT_PLANE_FAILURE_MODE") or "fallback_local"),
                "tool_runtime": str(os.getenv("TOOL_RUNTIME_FAILURE_MODE") or "fail_closed"),
                "judge": str(os.getenv("JUDGE_FAILURE_MODE") or "fail_closed"),
            },
        )
    else:
        fault_suite_report = {
            "schema_version": 2,
            "run_id": f"{run_id}-fault",
            "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "config": {"disabled": True},
            "cases": [],
            "summary": {
                "total_cases": 0,
                "hard_fail_cases": 0,
                "hard_fail_rate": 0.0,
                "fail_closed_count": 0,
                "fail_closed_rate": 0.0,
                "fault_containment_pass_count": 0,
                "fault_containment_pass_rate": 0.0,
                "mean_repair_steps": 0.0,
                "stop_reason_counts": [],
                "fault_type_counts": [],
                "effective_policy_counts": [],
            },
        }
    metrics["fault_suite"] = fault_suite_report
    metrics.update(extract_fault_metrics(fault_suite_report))

    scoreline = _build_scoreline(metrics)
    out_path = Path(args.out)
    artifact_dir = Path(args.artifact_dir)
    artifact_paths = _artifact_paths(out_path=out_path, artifact_dir=artifact_dir, run_id=run_id)
    resolved_config = build_resolved_config(args=vars(args), truth_pack=truth_pack)
    build_info = collect_build_info(repo_root=REPO_ROOT)
    config_fingerprint = str(resolved_config.get("config_fingerprint", ""))
    metrics["config_fingerprint"] = config_fingerprint
    scoreline["config_fingerprint"] = config_fingerprint
    created_at_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    scorecard = {
        "schema_version": int(BENCHMARK_SCHEMA_VERSION),
        "run_id": run_id,
        "created_at_utc": created_at_utc,
        "config_fingerprint": config_fingerprint,
        "git": _git_info(),
        "metrics": metrics,
        "scoreline": scoreline,
        "eval_set_manifest": eval_set_manifest,
        "per_task_breakdown": per_task_breakdown,
        "artifacts": artifact_paths,
        "inputs": {
            "split": args.split,
            "generator": args.generator,
            "generator_ref": args.generator,
            "tasks_dir": tasks_dir.as_posix(),
            "eval_set_fingerprint": eval_set_fingerprint,
            "config_fingerprint": config_fingerprint,
            "constitution_fingerprint": constitution.state.fingerprint,
            "constitution_version": constitution.state.version,
            "max_repairs": args.max_repairs,
            "coding_tasks": args.coding_tasks if not args.no_coding_suite else None,
            "coding_max_tasks": args.coding_max_tasks,
            "judge_gold_set": args.judge_gold_set if not args.no_judge_quality else None,
            "judge_quality_enabled": not bool(args.no_judge_quality),
            "fault_suite_enabled": not bool(args.no_fault_suite),
            "fault_max_cases": int(args.fault_max_cases),
            "fault_max_repair_steps": int(args.fault_max_repair_steps),
            "fault_soft_pass_threshold": float(args.fault_soft_pass_threshold),
        },
    }
    try:
        _write_benchmark_artifacts(
            scorecard=scorecard,
            artifact_paths=artifact_paths,
            resolved_config=resolved_config,
            build_info=build_info,
        )
    except Exception as exc:
        print(f"[benchmark] artifact integrity error: {exc}")
        return 2

    if args.print_json_line:
        print(f"[benchmark] scorecard: {json.dumps(scorecard, ensure_ascii=True)}")

    scoreline_line = _format_scoreline(scoreline)
    if args.print_scoreline:
        print(scoreline_line)
    try:
        _parse_scoreline(scoreline_line)
    except Exception as exc:
        print(f"[benchmark] scoreline parse failed: {exc}")
        return 2

    print("")
    print("Benchmark summary")
    print("-" * 17)
    print(f"PASS no repair: {metrics['pass_rate_no_repair']:.3f}")
    print(f"PASS with repair: {metrics['pass_rate_with_repair']:.3f}")
    print(f"Config fingerprint: {config_fingerprint}")
    print(f"Avg repairs used: {metrics['avg_repairs_used']:.3f}")
    print(f"Median repairs used: {metrics['median_repairs_used']:.3f}")
    print(f"Hard fail rate: {metrics['hard_fail_rate']:.3f}")
    print(f"Gate flip rate: {metrics['gate_flip_rate']:.3f}")
    print(f"Fail-closed rate: {float(metrics.get('fail_closed_rate', 0.0) or 0.0):.3f}")
    print(f"Fault containment pass rate: {float(metrics.get('fault_containment_pass_rate', 0.0) or 0.0):.3f}")
    print(f"Fault mean repair steps: {float(metrics.get('mean_repair_steps', 0.0) or 0.0):.3f}")
    print(f"Wasted repairs: {metrics['wasted_repairs']}")
    print(f"Fixed by 1 repair: {metrics['fixed_by_1']}")
    print(f"No-progress stops: {metrics['no_progress_stops']}")
    judge_quality_summary = metrics.get("judge_quality", {}) if isinstance(metrics.get("judge_quality"), dict) else {}
    print(f"Judge agreement: {float(judge_quality_summary.get('agreement', 0.0) or 0.0):.3f}")
    print(f"Judge precision: {float(judge_quality_summary.get('precision', 0.0) or 0.0):.3f}")
    print(f"Judge recall: {float(judge_quality_summary.get('recall', 0.0) or 0.0):.3f}")
    if metrics["top_failures"]:
        print("Top failure tags:")
        for item in metrics["top_failures"]:
            print(f"- {item['tag']}: {item['count']}")
    if metrics["top_hard_fail_tags"]:
        print("Top hard-fail tags:")
        for item in metrics["top_hard_fail_tags"]:
            print(f"- {item['tag']}: {item['count']}")

    if args.compare_to:
        ok, reasons = _compare_with_baseline(
            scorecard,
            args.compare_to,
            max_drop_pass_rate=args.max_drop_pass_rate,
            max_increase_avg_repairs=args.max_increase_avg_repairs,
        )
        if not ok:
            for r in reasons:
                print(f"[benchmark] regression: {r}")
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
