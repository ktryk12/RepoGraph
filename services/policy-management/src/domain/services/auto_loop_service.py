from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence
from datetime import datetime, timezone

from babyai_shared.core.orchestrator import EpisodeResult, run_episode
from babyai_shared.fingerprint import canonical_json, sha256_json
from policy.stagnation_stop_service import StagnationStopService, get_stagnation_stop_service
from babyai_shared.storage.safe_paths import safe_segment


DEFAULT_AUTOLOOP_ARTIFACT_ROOT = Path("artifacts") / "autoloop"

EpisodeRunner = Callable[[Dict[str, Any], Dict[str, Any] | str, Dict[str, Any]], EpisodeResult]


@dataclass(frozen=True)
class AutoLoopEpisodeSummary:
    index: int
    task_id: str
    passed: bool
    stop_reason: str
    failure_reasons: List[str]
    repairs_used: int
    episode_id: str
    tool_evidence_ref: str | None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": int(self.index),
            "task_id": str(self.task_id),
            "passed": bool(self.passed),
            "stop_reason": str(self.stop_reason),
            "failure_reasons": list(self.failure_reasons),
            "repairs_used": int(self.repairs_used),
            "episode_id": str(self.episode_id),
            "tool_evidence_ref": self.tool_evidence_ref,
        }


@dataclass(frozen=True)
class AutoLoopRunResult:
    run_id: str
    mode: str
    total_tasks: int
    episodes_run: int
    passed_count: int
    failed_count: int
    stop_reason: str
    episodes: List[AutoLoopEpisodeSummary]
    artifact_paths: Dict[str, str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "run_id": str(self.run_id),
            "mode": str(self.mode),
            "total_tasks": int(self.total_tasks),
            "episodes_run": int(self.episodes_run),
            "passed_count": int(self.passed_count),
            "failed_count": int(self.failed_count),
            "stop_reason": str(self.stop_reason),
            "episodes": [entry.to_dict() for entry in self.episodes],
            "artifact_paths": dict(self.artifact_paths),
        }


class AutoLoopService:
    """
    Shadow AutoLoop runner for scheduled jobs / CI trials.

    Guarantees:
    - artifact-only outputs
    - no mutation knobs are enabled
    - deterministic task ordering
    """

    def __init__(
        self,
        *,
        artifact_root: str | Path = DEFAULT_AUTOLOOP_ARTIFACT_ROOT,
        stagnation_stop: StagnationStopService | None = None,
        episode_runner: EpisodeRunner | None = None,
    ) -> None:
        self._artifact_root = Path(artifact_root)
        self._stagnation_stop = stagnation_stop or get_stagnation_stop_service()
        self._episode_runner = episode_runner or _default_episode_runner

    def run_shadow(
        self,
        *,
        tasks: Sequence[Mapping[str, Any] | Dict[str, Any]],
        truth_pack: Dict[str, Any] | str,
        knobs: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        max_episodes: int | None = None,
    ) -> AutoLoopRunResult:
        ordered_tasks = self._normalize_tasks(tasks)
        if not ordered_tasks:
            raise ValueError("tasks must not be empty")

        resolved_run_id = _optional_text(run_id) or _derived_run_id(ordered_tasks)
        limit = _coerce_limit(max_episodes, default=len(ordered_tasks))
        base_knobs = dict(knobs or {})
        self._assert_shadow_only_knobs(base_knobs)

        episodes: List[AutoLoopEpisodeSummary] = []
        failure_history: List[List[str]] = []
        stop_reason = "completed"

        for index, task in enumerate(ordered_tasks):
            if index >= limit:
                stop_reason = "max_episodes"
                break

            episode_knobs = self._shadow_knobs(
                base_knobs,
                run_id=resolved_run_id,
                episode_index=index,
            )
            result = self._episode_runner(dict(task), truth_pack, episode_knobs)
            summary = _episode_summary(index=index, result=result)
            episodes.append(summary)
            failure_history.append(list(summary.failure_reasons))

            stagnation = self._stagnation_stop.evaluate(
                tag_history=failure_history,
                repeat_window=2,
                require_score_delta=False,
            )
            if (not summary.passed) and stagnation.stop:
                stop_reason = "stagnation"
                break

        if stop_reason == "completed" and len(episodes) < len(ordered_tasks):
            stop_reason = "max_episodes"

        passed_count = sum(1 for item in episodes if item.passed)
        failed_count = len(episodes) - passed_count
        result = AutoLoopRunResult(
            run_id=resolved_run_id,
            mode="shadow",
            total_tasks=len(ordered_tasks),
            episodes_run=len(episodes),
            passed_count=passed_count,
            failed_count=failed_count,
            stop_reason=stop_reason,
            episodes=episodes,
            artifact_paths={},
        )
        artifact_paths = self._write_artifacts(result)
        return AutoLoopRunResult(
            run_id=result.run_id,
            mode=result.mode,
            total_tasks=result.total_tasks,
            episodes_run=result.episodes_run,
            passed_count=result.passed_count,
            failed_count=result.failed_count,
            stop_reason=result.stop_reason,
            episodes=list(result.episodes),
            artifact_paths=artifact_paths,
        )

    def _write_artifacts(self, result: AutoLoopRunResult) -> Dict[str, str]:
        from verify.artifacts.registry import write_artifact
        run_root = self._artifact_root / safe_segment(result.run_id)
        summary_path = run_root / "summary.json"
        episodes_path = run_root / "episodes.jsonl"

        summary_payload = result.to_dict()
        summary_payload["episodes_fingerprint"] = sha256_json([row.to_dict() for row in result.episodes])
        summary_payload["created_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        write_artifact(
            "autoloop_shadow_summary_json",
            summary_payload,
            summary_path,
            metadata={
                "job_id": result.run_id,
                "source_ref": "policy.auto_loop_service",
            },
        )

        lines = ""
        if result.episodes:
            lines = "\n".join(canonical_json(item.to_dict()) for item in result.episodes) + "\n"
        write_artifact(
            "autoloop_shadow_episodes_jsonl",
            lines,
            episodes_path,
            metadata={
                "job_id": result.run_id,
                "source_ref": "policy.auto_loop_service",
            },
        )
        return {
            "summary_json": summary_path.as_posix(),
            "episodes_jsonl": episodes_path.as_posix(),
        }

    def _shadow_knobs(
        self,
        knobs: Dict[str, Any],
        *,
        run_id: str,
        episode_index: int,
    ) -> Dict[str, Any]:
        out = dict(knobs)
        # Hard-disable mutation surfaces in shadow mode.
        out["promote_truth_enabled"] = False
        out["librarian_enabled"] = False
        out["autoloop_run_id"] = run_id
        out["autoloop_shadow_mode"] = True
        out["autoloop_episode_index"] = int(episode_index)
        return out

    def _assert_shadow_only_knobs(self, knobs: Dict[str, Any]) -> None:
        if bool(knobs.get("promote_truth_enabled")):
            raise ValueError("AutoLoopService shadow mode forbids promote_truth_enabled=true")
        if bool(knobs.get("librarian_enabled")):
            raise ValueError("AutoLoopService shadow mode forbids librarian_enabled=true")

    def _normalize_tasks(self, tasks: Sequence[Mapping[str, Any] | Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not isinstance(tasks, Sequence):
            raise ValueError("tasks must be a sequence")
        out: List[Dict[str, Any]] = []
        for raw in tasks:
            if isinstance(raw, Mapping):
                out.append(dict(raw))
        out.sort(key=lambda task: (_task_id(task, fallback=""), canonical_json(task)))
        return out


_AUTO_LOOP_SERVICE: AutoLoopService | None = None


def get_auto_loop_service(
    *,
    artifact_root: str | Path | None = None,
    reload: bool = False,
) -> AutoLoopService:
    global _AUTO_LOOP_SERVICE
    if _AUTO_LOOP_SERVICE is None or artifact_root is not None:
        _AUTO_LOOP_SERVICE = AutoLoopService(
            artifact_root=(artifact_root if artifact_root is not None else DEFAULT_AUTOLOOP_ARTIFACT_ROOT),
        )
        return _AUTO_LOOP_SERVICE
    if reload:
        _AUTO_LOOP_SERVICE = AutoLoopService(
            artifact_root=(artifact_root if artifact_root is not None else DEFAULT_AUTOLOOP_ARTIFACT_ROOT),
        )
    return _AUTO_LOOP_SERVICE


def _default_episode_runner(task: Dict[str, Any], truth_pack: Dict[str, Any] | str, knobs: Dict[str, Any]) -> EpisodeResult:
    return run_episode(task, truth_pack, knobs=knobs)


def _task_id(task: Dict[str, Any], fallback: str) -> str:
    task_id = _optional_text(task.get("task_id"))
    if task_id:
        return task_id
    spec = task.get("spec")
    if isinstance(spec, Mapping):
        spec_id = _optional_text(spec.get("id"))
        if spec_id:
            return spec_id
    return fallback


def _episode_summary(*, index: int, result: EpisodeResult) -> AutoLoopEpisodeSummary:
    payload = result.to_dict()
    telemetry = payload.get("telemetry", {})
    failure_reasons = telemetry.get("failure_reasons", [])
    normalized_reasons: List[str] = []
    if isinstance(failure_reasons, list):
        normalized_reasons = sorted({str(item).strip() for item in failure_reasons if str(item).strip()})
    return AutoLoopEpisodeSummary(
        index=index,
        task_id=str(payload.get("task_id") or "unknown"),
        passed=bool((telemetry or {}).get("passed", False)),
        stop_reason=str((telemetry or {}).get("stop_reason") or "unknown"),
        failure_reasons=normalized_reasons,
        repairs_used=int((telemetry or {}).get("repairs_used", 0)),
        episode_id=str(payload.get("episode_id") or ""),
        tool_evidence_ref=_optional_text(payload.get("tool_evidence_ref")),
    )


def _coerce_limit(value: int | None, *, default: int) -> int:
    if isinstance(value, int):
        return max(1, int(value))
    return max(1, int(default))


def _derived_run_id(tasks: Iterable[Mapping[str, Any] | Dict[str, Any]]) -> str:
    rows = []
    for task in tasks:
        task_obj = dict(task)
        rows.append({"task_id": _task_id(task_obj, fallback=""), "task": task_obj})
    digest = sha256_json(rows)[:16]
    return f"autoloop-{digest}"


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None
