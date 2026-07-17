from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import uuid

from jsonschema import Draft202012Validator

from agents.librarian_orchestrator import LibrarianOrchestrator
from services.aesa.aesa.application.use_cases.retrieve_context import RetrieveContextRequest
from services.aesa.aesa.bootstrap.wiring import build_retrieve_context_use_case
from babyai_shared.core.orchestrator import EpisodeResult, run_episode
from babyai_shared.core.question_gate import gate_question
from policy.capabilities import Capability
from babyai_shared.repobrain.snapshot import create_snapshot_artifact
from babyai_shared.tool_runtime.permissions import ToolRuntime, issue_token
from babyai_shared.tool_runtime.run import run_tool
from tools.base import ToolBudget
from tools.contracts import ToolResult, ToolRunRef, ToolTiming
from tools.repo_reader import RepoReaderTool


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schemas" / "coding_task.schema.json"
DEFAULT_TASKS_PATH = REPO_ROOT / "eval" / "coding" / "tasks_mvp.jsonl"


def main() -> int:
    p = argparse.ArgumentParser(description="Run coding suite tasks through the orchestrator loop.")
    p.add_argument("--tasks", default=str(DEFAULT_TASKS_PATH), help="Path to coding tasks JSONL.")
    p.add_argument("--max-tasks", type=int, default=None, help="Limit tasks executed.")
    p.add_argument("--out", default=None, help="Output JSON path (default: artifacts/coding_suite/<run_id>.json).")
    p.add_argument("--truth-pack", default="default", help="Truth pack alias or path.")
    p.add_argument("--promote-truth", action="store_true", help="Enable truth promotion.")
    p.add_argument("--no-promote-truth", action="store_true", help="Disable truth promotion (default).")
    p.add_argument("--snapshot-commit", default="HEAD", help="Snapshot commit for librarian retrieval.")
    p.add_argument("--librarian-registry", default="knowledge/registry.sqlite", help="Path to librarian registry.")
    p.add_argument("--librarian-artifact-root", default="artifacts", help="Artifact root for librarian snapshots.")
    p.add_argument("--no-librarians", action="store_true", help="Disable librarian retrieval.")
    p.add_argument("--max-librarian-hits", type=int, default=5, help="Top-k evidence lines per librarian.")
    args = p.parse_args()

    run_id = _run_id()
    out_path = Path(args.out) if args.out else Path("artifacts") / "coding_suite" / f"{run_id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload = run_coding_suite(
        tasks_path=Path(args.tasks),
        truth_pack=args.truth_pack,
        max_tasks=args.max_tasks,
        promote_truth=bool(args.promote_truth) and not bool(args.no_promote_truth),
        snapshot_commit=args.snapshot_commit,
        librarian_registry=args.librarian_registry,
        librarian_artifact_root=args.librarian_artifact_root,
        no_librarians=bool(args.no_librarians),
        max_librarian_hits=args.max_librarian_hits,
        run_id=run_id,
    )

    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[coding-suite] wrote {out_path}")
    print(f"[coding-suite] scoreline: {payload.get('scoreline')}")
    return 0


def run_coding_suite(
    *,
    tasks_path: Path,
    truth_pack: str,
    max_tasks: Optional[int],
    promote_truth: bool,
    snapshot_commit: str,
    librarian_registry: str,
    librarian_artifact_root: str,
    no_librarians: bool,
    max_librarian_hits: int,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    run_id = run_id or _run_id()
    tasks = _load_tasks(tasks_path)
    max_tasks = max_tasks if max_tasks and max_tasks > 0 else None

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    results: List[Dict[str, Any]] = []
    passed = 0
    executed = 0

    snapshot_ref = None
    retrieve_context_uc = build_retrieve_context_use_case(
        use_context_plane=False if no_librarians else None,
        artifact_root=librarian_artifact_root,
        repo_root=REPO_ROOT,
    )
    if not no_librarians:
        try:
            snapshot = create_snapshot_artifact(commit=snapshot_commit, context_id=f"coding-suite-{run_id}")
            snapshot_ref = snapshot.artifact_ref
            LibrarianOrchestrator(
                registry_path=librarian_registry,
                artifact_root=librarian_artifact_root,
            ).ensure_librarians(
                truth_pack=_truth_pack_for_orchestrator(truth_pack),
                snapshot_ref=str(snapshot_ref),
            )
        except Exception:
            snapshot_ref = None

    for idx, task in enumerate(tasks):
        if max_tasks is not None and idx >= max_tasks:
            break

        task_id = str(task.get("task_id", f"CODE-{idx+1:03d}"))
        context_id = f"coding-{run_id}-{task_id}"
        trace_id = f"{run_id}:{task_id}:{uuid.uuid4().hex[:8]}"

        entry: Dict[str, Any] = {
            "task_id": task_id,
            "status": "pending",
            "reasons": [],
            "routes": [],
            "context_refs": [],
            "tool_runs": [],
        }

        try:
            validator.validate(task)
        except Exception as exc:
            entry["status"] = "invalid"
            entry["reasons"] = [str(exc)]
            results.append(entry)
            continue

        prompt = str(task.get("prompt") or "")
        gate = gate_question(prompt, context={"context_id": context_id}, registry_path=librarian_registry)
        entry["routes"] = list(gate.routes)
        if not gate.allowed:
            entry["status"] = "denied"
            entry["reasons"] = list(gate.reasons)
            results.append(entry)
            continue

        # Context plane retrieval (best-effort)
        if gate.routes:
            entry["context_refs"] = retrieve_context_uc.execute(
                RetrieveContextRequest(
                    routes=list(gate.routes),
                    prompt=prompt,
                    context_id=context_id,
                    top_k=max_librarian_hits,
                    registry_path=librarian_registry,
                    run_id=run_id,
                    case_id=task_id,
                    trace_id=trace_id,
                    task_metadata={
                        "sensitivity": str(task.get("sensitivity") or "default"),
                        "task_id": task_id,
                    },
                )
            )

        # Tool runtime: repo read on first scoped path (best-effort)
        tool_runs, tool_results = _maybe_run_repo_reader(
            task,
            context_id=context_id,
            episode_id=run_id,
        )
        if tool_runs:
            entry["tool_runs"] = tool_runs

        eval_task = _to_eval_task(task, idx)
        knobs = {
            "promote_truth_enabled": promote_truth,
            "librarian_enabled": not no_librarians,
            "librarian_registry_path": librarian_registry,
            "librarian_artifact_root": librarian_artifact_root,
            "artifact_root": librarian_artifact_root,
            "autoloop_run_id": run_id,
            "data_need_scope_id": run_id,
            "trace_id": trace_id,
        }
        if tool_results:
            knobs["tool_results"] = list(tool_results)
        if snapshot_ref:
            knobs["snapshot_ref"] = str(snapshot_ref)

        _inject_intake_artifact(
            knobs=knobs,
            eval_task=eval_task,
            truth_pack=truth_pack,
            run_id=run_id,
        )
        _inject_effective_policy(
            knobs=knobs,
            eval_task=eval_task,
            truth_pack=truth_pack,
            run_id=run_id,
        )
        _inject_execution_permit(
            knobs=knobs,
            eval_task=eval_task,
            run_id=run_id,
        )

        episode = run_episode(eval_task, truth_pack, knobs=knobs)
        entry["episode"] = _episode_summary(episode)
        entry["status"] = "passed" if episode.telemetry.get("passed") else "failed"
        if entry["status"] == "passed":
            passed += 1
        executed += 1

        results.append(entry)

    metrics = _coding_metrics(results)
    scoreline = _scoreline(passed=passed, executed=executed, total=len(results))
    scoreline.update({
        "coding_pass_rate": metrics.get("pass_rate", 0.0),
        "coding_avg_repairs": metrics.get("avg_repairs_used", 0.0),
        "coding_scope_violation_rate": metrics.get("scope_violation_rate", 0.0),
    })

    return {
        "run_id": run_id,
        "generated_at": _utc_now(),
        "tasks_total": len(results),
        "tasks_executed": executed,
        "passed": passed,
        "metrics": metrics,
        "scoreline": scoreline,
        "results": results,
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


def _maybe_run_repo_reader(
    task: Dict[str, Any],
    *,
    context_id: str,
    episode_id: str,
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    scope = task.get("scope") if isinstance(task.get("scope"), dict) else {}
    paths = scope.get("paths") if isinstance(scope.get("paths"), list) else []
    if not paths:
        return [], []

    tools = {"repo_reader": RepoReaderTool()}
    runtime = ToolRuntime(tools=tools)
    token = issue_token(
        agent_id="coding-suite",
        episode_id=episode_id,
        scope_id=context_id,
        capabilities=[Capability.READ_REPO, Capability.WRITE_ARTIFACT],
    )
    budget = ToolBudget(max_bytes=20_000)

    records: List[Dict[str, Any]] = []
    evidence_rows: List[Dict[str, Any]] = []
    # Only run repo_read if explicitly allowed
    allowed = task.get("allowed_tools", [])
    if isinstance(allowed, list) and "repo_read" in allowed:
        request = {"path": str(paths[0])}
        record = run_tool(
            runtime,
            "repo_reader",
            request,
            budget=budget,
            token=token,
            required=Capability.READ_REPO,
            episode_id=episode_id,
            scope_id=context_id,
        )
        records.append(
            {
                "tool": "repo_reader",
                "ok": record.result.ok,
                "manifest_ref": record.manifest_ref,
                "output": record.result.output,
            }
        )
        started_at = str(record.manifest.get("started_at") or _utc_now())
        finished_at = str(record.manifest.get("finished_at") or _utc_now())
        run_ref = ToolRunRef(
            tool_id="repo_reader",
            artifact_ref=str(record.result.artifact_ref or record.manifest_ref),
            manifest_ref=str(record.manifest_ref),
        )
        timing = ToolTiming(
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=_duration_ms_iso(started_at, finished_at),
        )
        result_payload = ToolResult(
            tool_id="repo_reader",
            ok=bool(record.result.ok),
            output=dict(record.result.output or {}),
            run_ref=run_ref,
            timing=timing,
            warnings=[str(w) for w in (record.result.warnings or [])],
            cost=dict(record.result.cost or {}),
            error=None if bool(record.result.ok) else str((record.result.output or {}).get("error") or "tool_failed"),
            backend="tool_runtime",
        )
        evidence_rows.append(result_payload.to_dict())
    return records, evidence_rows


def _to_eval_task(task: Dict[str, Any], idx: int) -> Dict[str, Any]:
    task_id = str(task.get("task_id", f"CODE-{idx+1:03d}"))
    seq = _numeric_suffix(task_id, default=idx + 1)
    spec_id = f"spec_2026_02_11_{seq:04d}"
    prompt = str(task.get("prompt") or "Coding task")

    return {
        "task_id": f"EVAL-{seq:03d}",
        "spec": {
            "id": spec_id,
            "title": prompt[:120],
            "domain": "coding_tasks",
            "user_stories": [
                {
                    "id": "US-001",
                    "as_a": "developer",
                    "i_want": "to complete the coding task",
                    "so_that": "tests pass and behavior stays correct",
                    "acceptance_criteria": [
                        "All specified tests pass",
                        "Changes are limited to the defined scope",
                    ],
                }
            ],
            "nfr": {
                "security": {
                    "data_classification": "internal",
                    "auth": [],
                    "threat_model_required": False,
                    "rate_limiting_required": False,
                },
                "privacy": {
                    "gdpr": False,
                    "data_retention_days": 365,
                },
                "reliability": {
                    "sla": "99.0",
                    "rpo_minutes": 60,
                    "rto_minutes": 120,
                },
                "performance": {
                    "p95_latency_ms": 500,
                    "throughput_rps": 50,
                },
                "cost": {"monthly_budget_eur": 1000},
                "compliance": {"pci_dss": False, "sox": False, "hipaa": False},
                "operability": {"observability_level": "basic", "audit_log_required": False},
            },
            "constraints": {
                "team_size": 2,
                "time_to_mvp_days": 30,
                "preferred_stack": ["python"],
                "deployment": "docker",
                "must_support": [],
            },
        },
        "expected": {
            "allowed_styles": ["modular_monolith", "hybrid", "microservices"],
            "must_include": [],
            "forbidden": [],
        },
        "scoring": {"functional": 0.4, "security": 0.3, "architecture_fit": 0.3},
    }


def _episode_summary(episode: EpisodeResult) -> Dict[str, Any]:
    return {
        "episode_id": episode.episode_id,
        "task_id": episode.task_id,
        "tool_evidence_ref": episode.tool_evidence_ref,
        "passed": episode.eval_result.get("passed"),
        "scores": episode.eval_result.get("scores"),
        "penalties": episode.eval_result.get("penalties"),
        "telemetry": episode.telemetry,
        "artifacts": episode.artifacts,
    }


def _numeric_suffix(text: str, *, default: int) -> int:
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        try:
            return int(digits[-4:])
        except Exception:
            return default
    return default


def _duration_ms_iso(started_at: str, finished_at: str) -> float:
    start = _parse_iso(started_at)
    end = _parse_iso(finished_at)
    return max(0.0, (end - start).total_seconds() * 1000.0)


def _parse_iso(value: str) -> datetime:
    text = str(value).strip()
    normalized = text.replace("Z", "+00:00") if text.endswith("Z") else text
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _truth_pack_for_orchestrator(truth_pack: str) -> Dict[str, Any]:
    from babyai_shared.truth.loader import load_truth_pack

    return load_truth_pack(str(truth_pack))


def _inject_intake_artifact(
    *,
    knobs: Dict[str, Any],
    eval_task: Dict[str, Any],
    truth_pack: str,
    run_id: str,
) -> None:
    from services.aesa.aesa.application.use_cases.intake import IntakeUseCase
    from babyai_shared.fingerprint import canonical_json

    if "intake_artifact" in knobs:
        return
    _ = run_id
    payload = canonical_json(
        {
            "task": eval_task,
            "truth_pack": str(truth_pack),
        }
    )
    artifact = IntakeUseCase().ingest(
        source_type="coding_suite",
        content=payload,
        classification="low",
    )
    knobs["intake_artifact"] = artifact.to_dict()


def _inject_effective_policy(
    *,
    knobs: Dict[str, Any],
    eval_task: Dict[str, Any],
    truth_pack: str,
    run_id: str,
) -> None:
    if "effective_policy" in knobs:
        return
    _ = (eval_task, truth_pack, run_id)
    knobs["effective_policy"] = {
        "policy_id": "smoke_policy",
        "policy_version": 1,
        "write_scope": {"type": "compliance_docs"},
    }


def _inject_execution_permit(
    *,
    knobs: Dict[str, Any],
    eval_task: Dict[str, Any],
    run_id: str,
) -> None:
    if "execution_permit" in knobs or "approval_token" in knobs:
        return
    effective_policy = knobs.get("effective_policy")
    if not isinstance(effective_policy, dict):
        return
    from policy.approval_gate import compute_policy_fingerprint

    decision_id = str(knobs.get("decision_id") or eval_task.get("task_id") or run_id).strip() or run_id
    policy_fingerprint = compute_policy_fingerprint(effective_policy)
    permit = {
        "decision_id": decision_id,
        "policy_fingerprint": policy_fingerprint,
        "approved_by": "coding_suite",
        "approved_at": _utc_now(),
        "reason": "AUTO_APPROVE_CODING_SUITE",
    }
    knobs["decision_id"] = decision_id
    knobs["policy_fingerprint"] = policy_fingerprint
    knobs["execution_permit"] = permit


def _run_id() -> str:
    return f"coding-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _scoreline(*, passed: int, executed: int, total: int) -> Dict[str, Any]:
    rate = (passed / executed) if executed else 0.0
    return {
        "pass_rate": round(rate, 3),
        "passed": passed,
        "executed": executed,
        "total": total,
    }


def _coding_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    executed = [r for r in results if r.get("status") in {"passed", "failed"}]
    passed = [r for r in executed if r.get("status") == "passed"]

    repairs: List[int] = []
    scope_violations = 0

    for entry in executed:
        episode = entry.get("episode", {}) if isinstance(entry.get("episode"), dict) else {}
        telemetry = episode.get("telemetry", {}) if isinstance(episode.get("telemetry"), dict) else {}
        if isinstance(telemetry.get("repairs_used"), int):
            repairs.append(int(telemetry["repairs_used"]))

        penalties = episode.get("penalties") if isinstance(episode.get("penalties"), list) else []
        if any("scope_violation" in str(p) for p in penalties):
            scope_violations += 1
            continue

        tool_runs = entry.get("tool_runs")
        if isinstance(tool_runs, list):
            for run in tool_runs:
                if not isinstance(run, dict):
                    continue
                output = run.get("output")
                if isinstance(output, dict) and output.get("error") == "scope_violation":
                    scope_violations += 1
                    break

    pass_rate = (len(passed) / len(executed)) if executed else 0.0
    avg_repairs = (sum(repairs) / len(repairs)) if repairs else 0.0
    scope_violation_rate = (scope_violations / len(executed)) if executed else 0.0

    return {
        "pass_rate": round(pass_rate, 3),
        "avg_repairs_used": round(avg_repairs, 3),
        "scope_violation_rate": round(scope_violation_rate, 3),
        "tasks_executed": len(executed),
        "tasks_total": len(results),
    }


if __name__ == "__main__":
    raise SystemExit(main())
