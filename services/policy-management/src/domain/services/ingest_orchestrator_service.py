from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Sequence, Tuple

from babyai_shared.fingerprint import sha256_json
from babyai_shared.storage.safe_paths import safe_segment


IngestPipelineHandler = Callable[[Dict[str, Any]], Mapping[str, Any] | Dict[str, Any]]

_SOURCE_ALIASES = {
    "git": "git",
    "github": "git",
    "hf": "hf",
    "huggingface": "hf",
    "pdf": "pdf",
    "yt": "yt",
    "youtube": "yt",
}

_TERMINAL_STATES = {"SUCCEEDED", "FAILED"}
_ALLOWED_TRANSITIONS = {
    "PENDING": {"QUEUED"},
    "QUEUED": {"RUNNING"},
    "RUNNING": _TERMINAL_STATES,
    "SUCCEEDED": set(),
    "FAILED": set(),
}


@dataclass(frozen=True)
class IngestJobRequest:
    source_type: str
    source_ref: str
    payload: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    job_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_type", _normalized_source_type(self.source_type))
        object.__setattr__(self, "source_ref", _required_text(self.source_ref, name="source_ref"))
        object.__setattr__(self, "payload", _as_dict(self.payload))
        object.__setattr__(self, "metadata", _as_dict(self.metadata))
        object.__setattr__(self, "job_id", _optional_text(self.job_id))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class IngestJobResult:
    job_id: str
    source_type: str
    source_ref: str
    state: str
    state_history: List[str]
    output: Dict[str, Any]
    error: str | None
    artifact_paths: Dict[str, str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "state": self.state,
            "state_history": list(self.state_history),
            "output": dict(self.output),
            "error": self.error,
            "artifact_paths": dict(self.artifact_paths),
        }


@dataclass(frozen=True)
class IngestBatchResult:
    episode_id: str
    job_count: int
    succeeded: int
    failed: int
    state_machine_fingerprint: str
    jobs: List[IngestJobResult]
    artifact_paths: Dict[str, str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "job_count": int(self.job_count),
            "succeeded": int(self.succeeded),
            "failed": int(self.failed),
            "state_machine_fingerprint": self.state_machine_fingerprint,
            "jobs": [item.to_dict() for item in self.jobs],
            "artifact_paths": dict(self.artifact_paths),
        }


@dataclass
class _JobRuntime:
    request: IngestJobRequest
    artifact_paths: Dict[str, str]
    state: str = "PENDING"
    state_history: List[str] = field(default_factory=lambda: ["PENDING"])
    output: Dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class IngestOrchestratorService:
    """
    Deterministic ingest episode orchestrator with bounded worker pool.

    Determinism strategy:
    - job ids are deterministic when omitted
    - execution submission order is sorted by job_id
    - state transitions are applied in sorted job order
    - result aggregation is sorted by job_id
    """

    def __init__(
        self,
        *,
        artifact_root: str | Path = Path("artifacts") / "ingest",
        max_workers: int = 4,
        pipelines: Mapping[str, IngestPipelineHandler] | None = None,
    ) -> None:
        self._artifact_root = Path(artifact_root)
        self._max_workers = max(1, int(max_workers))
        self._pipelines: Dict[str, IngestPipelineHandler] = _default_pipelines()
        if isinstance(pipelines, Mapping):
            for source_type, handler in pipelines.items():
                self.register_pipeline(str(source_type), handler)

    def register_pipeline(self, source_type: str, handler: IngestPipelineHandler) -> None:
        if not callable(handler):
            raise ValueError("pipeline handler must be callable")
        self._pipelines[_normalized_source_type(source_type)] = handler

    def run_jobs(
        self,
        jobs: Sequence[IngestJobRequest | Mapping[str, Any]],
        *,
        episode_id: str | None = None,
    ) -> IngestBatchResult:
        requests = self._normalize_requests(jobs)
        if not requests:
            raise ValueError("jobs must not be empty")
        ordered = sorted(requests, key=lambda item: item.job_id or "")
        resolved_episode_id = _resolve_episode_id(ordered, explicit=episode_id)

        runtimes: List[_JobRuntime] = []
        for request in ordered:
            runtime = _JobRuntime(
                request=request,
                artifact_paths=self._job_artifact_paths(
                    episode_id=resolved_episode_id,
                    job_id=str(request.job_id),
                ),
            )
            self._write_state_artifacts(runtime, episode_id=resolved_episode_id)
            runtimes.append(runtime)

        for runtime in runtimes:
            self._transition(runtime, "QUEUED", episode_id=resolved_episode_id)

        futures: Dict[str, Future[Tuple[bool, Dict[str, Any], str | None]]] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="ingest-worker") as executor:
            for runtime in runtimes:
                self._transition(runtime, "RUNNING", episode_id=resolved_episode_id)
                futures[runtime.request.job_id or ""] = executor.submit(self._invoke_pipeline, runtime.request)

            for runtime in runtimes:
                future = futures[runtime.request.job_id or ""]
                ok, output, error = future.result()
                runtime.output = dict(output)
                runtime.error = _optional_text(error)
                next_state = "SUCCEEDED" if ok else "FAILED"
                self._transition(runtime, next_state, episode_id=resolved_episode_id)

        results = [self._job_result(runtime) for runtime in runtimes]
        succeeded = sum(1 for item in results if item.state == "SUCCEEDED")
        failed = len(results) - succeeded
        state_machine_fingerprint = sha256_json(
            {
                item.job_id: item.state_history
                for item in results
            }
        )
        summary = {
            "schema_version": 1,
            "episode_id": resolved_episode_id,
            "job_count": len(results),
            "succeeded": int(succeeded),
            "failed": int(failed),
            "state_machine_fingerprint": state_machine_fingerprint,
            "jobs": [item.to_dict() for item in results],
        }
        summary_path = self._episode_root(resolved_episode_id) / "summary.json"
        from verify.artifacts.registry import write_artifact
        write_artifact(
            "ingest_episode_summary_json",
            summary,
            summary_path,
            metadata={
                "job_id": resolved_episode_id,
                "source_ref": "policy.ingest_orchestrator_service",
            },
        )
        return IngestBatchResult(
            episode_id=resolved_episode_id,
            job_count=len(results),
            succeeded=succeeded,
            failed=failed,
            state_machine_fingerprint=state_machine_fingerprint,
            jobs=results,
            artifact_paths={"summary_json": summary_path.as_posix()},
        )

    def _normalize_requests(self, jobs: Sequence[IngestJobRequest | Mapping[str, Any]]) -> List[IngestJobRequest]:
        if not isinstance(jobs, Sequence):
            raise ValueError("jobs must be a sequence")
        out: List[IngestJobRequest] = []
        seen_ids: set[str] = set()
        for idx, raw in enumerate(jobs):
            request = _to_request(raw)
            resolved_job_id = request.job_id or _derived_job_id(request=request, index=idx)
            normalized = IngestJobRequest(
                job_id=resolved_job_id,
                source_type=request.source_type,
                source_ref=request.source_ref,
                payload=request.payload,
                metadata=request.metadata,
            )
            if normalized.job_id in seen_ids:
                raise ValueError(f"duplicate job_id: {normalized.job_id}")
            seen_ids.add(str(normalized.job_id))
            out.append(normalized)
        return out

    def _invoke_pipeline(self, request: IngestJobRequest) -> Tuple[bool, Dict[str, Any], str | None]:
        handler = self._pipelines.get(request.source_type)
        if handler is None:
            return False, {}, f"unknown_source_type:{request.source_type}"
        try:
            raw = handler(request.to_dict())
            if isinstance(raw, Mapping):
                return True, dict(raw), None
            if raw is None:
                return True, {}, None
            return True, {"value": raw}, None
        except Exception as exc:
            return False, {}, f"{type(exc).__name__}:{exc}"

    def _transition(self, runtime: _JobRuntime, next_state: str, *, episode_id: str) -> None:
        current = str(runtime.state)
        allowed = _ALLOWED_TRANSITIONS.get(current, set())
        if next_state not in allowed:
            raise ValueError(f"invalid ingest state transition: {current} -> {next_state}")
        runtime.state = str(next_state)
        runtime.state_history.append(str(next_state))
        self._write_state_artifacts(runtime, episode_id=episode_id)

    def _write_state_artifacts(self, runtime: _JobRuntime, *, episode_id: str) -> None:
        from verify.artifacts.registry import write_artifact
        state_payload = {
            "schema_version": 1,
            "episode_id": episode_id,
            "job_id": runtime.request.job_id,
            "source_type": runtime.request.source_type,
            "source_ref": runtime.request.source_ref,
            "state": runtime.state,
            "state_index": len(runtime.state_history) - 1,
            "error": runtime.error,
        }
        history_payload = {
            "schema_version": 1,
            "episode_id": episode_id,
            "job_id": runtime.request.job_id,
            "state_history": list(runtime.state_history),
        }
        write_artifact(
            "ingest_job_state_json",
            state_payload,
            Path(runtime.artifact_paths["state_json"]),
            metadata={
                "job_id": runtime.request.job_id,
                "source_ref": runtime.request.source_ref,
            },
        )
        write_artifact(
            "ingest_job_state_history_json",
            history_payload,
            Path(runtime.artifact_paths["state_history_json"]),
            metadata={
                "job_id": runtime.request.job_id,
                "source_ref": runtime.request.source_ref,
            },
        )
        if runtime.state in _TERMINAL_STATES:
            result_payload = {
                "schema_version": 1,
                "episode_id": episode_id,
                "job_id": runtime.request.job_id,
                "source_type": runtime.request.source_type,
                "source_ref": runtime.request.source_ref,
                "state": runtime.state,
                "state_history": list(runtime.state_history),
                "output": dict(runtime.output),
                "error": runtime.error,
            }
            write_artifact(
                "ingest_job_result_json",
                result_payload,
                Path(runtime.artifact_paths["result_json"]),
                metadata={
                    "job_id": runtime.request.job_id,
                    "source_ref": runtime.request.source_ref,
                },
            )

    def _job_result(self, runtime: _JobRuntime) -> IngestJobResult:
        return IngestJobResult(
            job_id=str(runtime.request.job_id or ""),
            source_type=str(runtime.request.source_type),
            source_ref=str(runtime.request.source_ref),
            state=str(runtime.state),
            state_history=list(runtime.state_history),
            output=dict(runtime.output),
            error=runtime.error,
            artifact_paths=dict(runtime.artifact_paths),
        )

    def _episode_root(self, episode_id: str) -> Path:
        return self._artifact_root / safe_segment(str(episode_id))

    def _job_artifact_paths(self, *, episode_id: str, job_id: str) -> Dict[str, str]:
        root = self._episode_root(episode_id) / "jobs" / safe_segment(str(job_id))
        return {
            "state_json": (root / "state.json").as_posix(),
            "state_history_json": (root / "state_history.json").as_posix(),
            "result_json": (root / "result.json").as_posix(),
        }


_INGEST_ORCHESTRATOR_SERVICE: IngestOrchestratorService | None = None


def get_ingest_orchestrator_service(
    *,
    artifact_root: str | Path | None = None,
    max_workers: int | None = None,
    reload: bool = False,
) -> IngestOrchestratorService:
    global _INGEST_ORCHESTRATOR_SERVICE
    if _INGEST_ORCHESTRATOR_SERVICE is None or reload or artifact_root is not None or max_workers is not None:
        _INGEST_ORCHESTRATOR_SERVICE = IngestOrchestratorService(
            artifact_root=(artifact_root if artifact_root is not None else Path("artifacts") / "ingest"),
            max_workers=(max_workers if isinstance(max_workers, int) else 4),
        )
    return _INGEST_ORCHESTRATOR_SERVICE


def _to_request(raw: IngestJobRequest | Mapping[str, Any]) -> IngestJobRequest:
    if isinstance(raw, IngestJobRequest):
        return raw
    if not isinstance(raw, Mapping):
        raise ValueError("job payload must be an object")
    source_type = raw.get("source_type")
    if source_type is None:
        source_type = raw.get("source")
    if source_type is None:
        source_type = raw.get("kind")
    source_ref = raw.get("source_ref")
    if source_ref is None:
        source_ref = raw.get("ref")
    return IngestJobRequest(
        job_id=_optional_text(raw.get("job_id")),
        source_type=str(source_type or ""),
        source_ref=str(source_ref or ""),
        payload=_as_dict(raw.get("payload")),
        metadata=_as_dict(raw.get("metadata")),
    )


def _default_pipelines() -> Dict[str, IngestPipelineHandler]:
    return {
        "git": _stub_pipeline_git,
        "hf": _stub_pipeline_hf,
        "pdf": _stub_pipeline_pdf,
        "yt": _stub_pipeline_yt,
    }


def _stub_pipeline_git(job: Dict[str, Any]) -> Dict[str, Any]:
    payload = _as_dict(job.get("payload"))
    repo_path = _optional_text(payload.get("repo_path")) or _optional_text(payload.get("repo"))
    commit_hash = _optional_text(payload.get("commit_hash")) or _optional_text(payload.get("commit"))
    if repo_path and commit_hash:
        try:
            from policy.git_ingest_service import GitIngestService

            result = GitIngestService().ingest(
                repo_path=repo_path,
                commit_hash=commit_hash,
                source_ref=_optional_text(job.get("source_ref")),
            )
            return {
                "source_type": "git",
                "source_ref": str(job.get("source_ref") or ""),
                "status": str(result.status),
                "commit_hash": str(result.commit_hash),
                "manifest_path": str(result.manifest_path),
                "spdx_findings": [item.to_dict() for item in result.spdx_findings],
                "secret_findings": [item.to_dict() for item in result.secret_findings],
            }
        except Exception as exc:
            return {
                "source_type": "git",
                "source_ref": str(job.get("source_ref") or ""),
                "status": "error",
                "error": f"git_ingest_error:{type(exc).__name__}:{exc}",
            }
    return _stub_pipeline_result("git", job)


def _stub_pipeline_hf(job: Dict[str, Any]) -> Dict[str, Any]:
    return _stub_pipeline_result("hf", job)


def _stub_pipeline_pdf(job: Dict[str, Any]) -> Dict[str, Any]:
    return _stub_pipeline_result("pdf", job)


def _stub_pipeline_yt(job: Dict[str, Any]) -> Dict[str, Any]:
    return _stub_pipeline_result("yt", job)


def _stub_pipeline_result(source_type: str, job: Dict[str, Any]) -> Dict[str, Any]:
    source_ref = _required_text(job.get("source_ref"), name="source_ref")
    digest = sha256_json(
        {
            "source_type": source_type,
            "source_ref": source_ref,
            "payload": _as_dict(job.get("payload")),
        }
    )
    return {
        "source_type": str(source_type),
        "source_ref": source_ref,
        "status": "accepted",
        "pack_ref": f"artifact:sha256:{digest}",
        "pack_kind": "ingest_skeleton",
    }


def _resolve_episode_id(requests: List[IngestJobRequest], *, explicit: str | None) -> str:
    value = _optional_text(explicit)
    if value:
        return value
    digest = sha256_json([str(item.job_id) for item in requests])[:16]
    return f"ingest-{digest}"


def _derived_job_id(*, request: IngestJobRequest, index: int) -> str:
    digest = sha256_json(
        {
            "source_type": request.source_type,
            "source_ref": request.source_ref,
            "payload": request.payload,
            "metadata": request.metadata,
        }
    )[:16]
    return f"{request.source_type}-{digest}-{int(index):04d}"


def _normalized_source_type(value: Any) -> str:
    source = _required_text(value, name="source_type").lower()
    return _SOURCE_ALIASES.get(source, source)


def _required_text(value: Any, *, name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}
