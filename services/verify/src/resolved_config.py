from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from verify.artifacts.registry import write_artifact


RESOLVED_CONFIG_SCHEMA_VERSION = 1


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _parse_env_bool(raw: Optional[str]) -> Optional[bool]:
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def compute_config_fingerprint(config: Dict[str, Any]) -> str:
    canonical = _canonical_json(config)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_resolved_config(
    *,
    args: Mapping[str, Any],
    truth_pack: Mapping[str, Any],
    env: Mapping[str, str] | None = None,
) -> Dict[str, Any]:
    source_env = env if env is not None else os.environ
    from policy.constitution_service import get_constitution_service
    constitution = get_constitution_service()

    gates = truth_pack.get("gates", {}) if isinstance(truth_pack.get("gates"), dict) else {}
    budgets = truth_pack.get("budgets", {}) if isinstance(truth_pack.get("budgets"), dict) else {}
    layer_info = truth_pack.get("layers", {}) if isinstance(truth_pack.get("layers"), dict) else {}

    config: Dict[str, Any] = {
        "benchmark": {
            "tasks_dir": str(args.get("tasks_dir", "")),
            "split": str(args.get("split", "")) if args.get("split") is not None else None,
            "generator": str(args.get("generator", "")),
            "coding_tasks": (str(args.get("coding_tasks")) if args.get("coding_tasks") is not None else None),
            "judge_gold_set": (str(args.get("judge_gold_set")) if args.get("judge_gold_set") is not None else None),
        },
        "feature_flags": {
            "no_coding_suite": bool(args.get("no_coding_suite", False)),
            "no_judge_quality": bool(args.get("no_judge_quality", False)),
            "coding_no_librarians": bool(args.get("coding_no_librarians", False)),
            "append_log": bool(args.get("append_log", False)),
            "print_json_line": bool(args.get("print_json_line", True)),
            "print_scoreline": bool(args.get("print_scoreline", True)),
            "writes_enabled": _parse_env_bool(source_env.get("WRITES_ENABLED")),
            "ingest_write_enabled": _parse_env_bool(source_env.get("INGEST_WRITE")),
            "train_write_enabled": _parse_env_bool(source_env.get("TRAIN_WRITE")),
            "policy_adopt_enabled": _parse_env_bool(source_env.get("POLICY_ADOPT")),
            "promote_active_enabled": _parse_env_bool(source_env.get("PROMOTE_ACTIVE")),
            "tool_runner_enabled": _parse_env_bool(source_env.get("FEATURE_TOOL_RUNNER")),
            "tool_evidence_gate_enabled": _parse_env_bool(source_env.get("FEATURE_TOOL_EVIDENCE_GATE")),
            "use_tool_runtime_remote": _parse_env_bool(source_env.get("USE_TOOL_RUNTIME_REMOTE")),
            "use_context_plane": _parse_env_bool(source_env.get("USE_CONTEXT_PLANE")),
        },
        "thresholds": {
            "bench_pass_threshold": source_env.get("BENCH_PASS_THRESHOLD"),
            "coding_pass_threshold": source_env.get("CODING_PASS_THRESHOLD"),
            "swarm_pass_threshold": source_env.get("SWARM_PASS_THRESHOLD"),
            "max_drop_pass_rate": float(args.get("max_drop_pass_rate", 0.0)),
            "max_increase_avg_repairs": float(args.get("max_increase_avg_repairs", 0.0)),
            "hard_gate_toggles": {
                "truth_ci_mode": bool(gates.get("ci_mode", False)),
                "tool_evidence_gate_enabled": _parse_env_bool(source_env.get("FEATURE_TOOL_EVIDENCE_GATE")),
            },
        },
        "performance_budgets": {
            "max_steps": int(args.get("max_steps", 0)),
            "max_repairs": int(args.get("max_repairs", 0)),
            "coding_max_tasks": (
                int(args.get("coding_max_tasks")) if args.get("coding_max_tasks") is not None else None
            ),
            "truth_defaults": {
                "max_steps": int(budgets.get("max_steps", 0)) if budgets.get("max_steps") is not None else None,
                "max_repairs": int(budgets.get("max_repairs", 0)) if budgets.get("max_repairs") is not None else None,
            },
        },
        "model_refs": {
            "generator_ref": str(args.get("generator", "")),
            "router_model_ref": source_env.get("AESA_ROUTER_MODEL"),
            "repair_policy_model_ref": source_env.get("AESA_REPAIR_POLICY_MODEL"),
            "runner_id": source_env.get("RUNNER_ID"),
            "generator_id": source_env.get("GENERATOR_ID"),
        },
        "tools": {
            "mode": str(
                source_env.get("TOOL_RUNTIME_MODE")
                or source_env.get("TOOLS_MODE")
                or ("disabled" if _parse_env_bool(source_env.get("FEATURE_TOOL_RUNNER")) is False else "real")
            ),
            "teacher_mode": source_env.get("TEACHER_MODE"),
            "runtime_base_url": source_env.get("TOOL_RUNTIME_BASE_URL"),
            "api_key_configured": bool(str(source_env.get("TOOL_RUNTIME_API_KEY", "")).strip()),
            "runtime_transport": (
                "remote" if _parse_env_bool(source_env.get("USE_TOOL_RUNTIME_REMOTE")) else "local"
            ),
        },
        "truth_pack": {
            "version": truth_pack.get("version"),
            "pack_hash": truth_pack.get("pack_hash"),
            "baseline_hash": truth_pack.get("baseline_hash"),
            "layers": layer_info,
        },
        "constitution": {
            "version": constitution.state.version,
            "fingerprint": constitution.state.fingerprint,
        },
    }

    fingerprint = compute_config_fingerprint(config)
    return {
        "schema_version": int(RESOLVED_CONFIG_SCHEMA_VERSION),
        "created_at_utc": _now_utc_iso(),
        "config_fingerprint": fingerprint,
        "constitution": constitution.metadata(),
        "config": config,
    }


def write_resolved_config(path: Path, payload: Dict[str, Any]) -> None:
    from policy.constitution_service import get_constitution_service
    constitution = get_constitution_service()
    write_artifact(
        "resolved_config_json",
        payload,
        path,
        metadata={
            "source_ref": "verify.resolved_config",
            "constitution_version": constitution.state.version,
        },
    )
