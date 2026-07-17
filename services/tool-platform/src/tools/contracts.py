from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List
import json

from jsonschema import Draft202012Validator
from referencing import Registry, Resource


TOOL_RESULT = "tool_result"
TOOL_EVIDENCE_PACK = "tool_evidence_pack"

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
_SCHEMA_FILE_BY_NAME = {
    TOOL_RESULT: _SCHEMA_DIR / "tool_result.schema.json",
    TOOL_EVIDENCE_PACK: _SCHEMA_DIR / "tool_evidence_pack.schema.json",
}


class ToolContractValidationError(ValueError):
    def __init__(self, *, schema_name: str, message: str) -> None:
        self.schema_name = str(schema_name)
        super().__init__(f"{self.schema_name}: {message}")


@dataclass(frozen=True)
class ToolTiming:
    started_at: str
    finished_at: str
    duration_ms: float

    def __post_init__(self) -> None:
        started = _non_empty("started_at", self.started_at)
        finished = _non_empty("finished_at", self.finished_at)
        duration = float(self.duration_ms)
        if duration < 0.0:
            raise ValueError("duration_ms must be >= 0")
        object.__setattr__(self, "started_at", started)
        object.__setattr__(self, "finished_at", finished)
        object.__setattr__(self, "duration_ms", duration)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": self.duration_ms,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ToolTiming":
        if not isinstance(payload, dict):
            raise ValueError("tool_timing payload must be an object")
        return cls(
            started_at=str(payload.get("started_at") or ""),
            finished_at=str(payload.get("finished_at") or ""),
            duration_ms=float(payload.get("duration_ms", 0.0) or 0.0),
        )


@dataclass(frozen=True)
class ToolRunRef:
    tool_id: str
    artifact_ref: str
    manifest_ref: str | None = None

    def __post_init__(self) -> None:
        tool_id = _non_empty("tool_id", self.tool_id)
        artifact_ref = _non_empty("artifact_ref", self.artifact_ref)
        manifest_ref = _optional_str(self.manifest_ref)
        object.__setattr__(self, "tool_id", tool_id)
        object.__setattr__(self, "artifact_ref", artifact_ref)
        object.__setattr__(self, "manifest_ref", manifest_ref)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "artifact_ref": self.artifact_ref,
            "manifest_ref": self.manifest_ref,
        }

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ToolRunRef":
        if not isinstance(payload, dict):
            raise ValueError("tool_run_ref payload must be an object")
        return cls(
            tool_id=str(payload.get("tool_id") or ""),
            artifact_ref=str(payload.get("artifact_ref") or ""),
            manifest_ref=_optional_str(payload.get("manifest_ref")),
        )


@dataclass(frozen=True)
class ToolResult:
    tool_id: str
    ok: bool
    output: Dict[str, Any]
    run_ref: ToolRunRef
    timing: ToolTiming
    warnings: List[str] = field(default_factory=list)
    cost: Dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    backend: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        tool_id = _non_empty("tool_id", self.tool_id)
        output = _as_dict("output", self.output)
        warnings = _string_list(self.warnings)
        cost = _as_dict("cost", self.cost)
        error = _optional_str(self.error)
        backend = _optional_str(self.backend)
        schema_version = int(self.schema_version)
        if schema_version != 1:
            raise ValueError("schema_version must be 1")

        object.__setattr__(self, "tool_id", tool_id)
        object.__setattr__(self, "output", output)
        object.__setattr__(self, "warnings", warnings)
        object.__setattr__(self, "cost", cost)
        object.__setattr__(self, "error", error)
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "schema_version", schema_version)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tool_id": self.tool_id,
            "ok": bool(self.ok),
            "output": dict(self.output),
            "run_ref": self.run_ref.to_dict(),
            "timing": self.timing.to_dict(),
            "warnings": list(self.warnings),
            "cost": dict(self.cost),
            "error": self.error,
            "backend": self.backend,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ToolResult":
        if not isinstance(payload, dict):
            raise ValueError("tool_result payload must be an object")
        return cls(
            tool_id=str(payload.get("tool_id") or ""),
            ok=bool(payload.get("ok", False)),
            output=_as_dict("output", payload.get("output")),
            run_ref=ToolRunRef.from_dict(_as_dict("run_ref", payload.get("run_ref"))),
            timing=ToolTiming.from_dict(_as_dict("timing", payload.get("timing"))),
            warnings=_string_list(payload.get("warnings", [])),
            cost=_as_dict("cost", payload.get("cost", {})),
            error=_optional_str(payload.get("error")),
            backend=_optional_str(payload.get("backend")),
            schema_version=int(payload.get("schema_version", 1) or 1),
        )

    @classmethod
    def from_json(cls, raw: str) -> "ToolResult":
        parsed = json.loads(str(raw))
        if not isinstance(parsed, dict):
            raise ValueError("tool_result JSON must decode to object")
        return cls.from_dict(parsed)


@dataclass(frozen=True)
class ToolEvidencePack:
    tool_runs: List[ToolRunRef]
    tool_results: List[ToolResult]
    run_id: str | None = None
    case_id: str | None = None
    trace_id: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.tool_runs:
            raise ValueError("tool_runs must not be empty")
        if not self.tool_results:
            raise ValueError("tool_results must not be empty")
        if int(self.schema_version) != 1:
            raise ValueError("schema_version must be 1")

        run_id = _optional_str(self.run_id)
        case_id = _optional_str(self.case_id)
        trace_id = _optional_str(self.trace_id)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "case_id", case_id)
        object.__setattr__(self, "trace_id", trace_id)
        object.__setattr__(self, "schema_version", 1)

    def to_dict(self) -> Dict[str, Any]:
        run_rows = sorted(
            [row.to_dict() for row in self.tool_runs],
            key=lambda item: (
                str(item.get("tool_id") or ""),
                str(item.get("artifact_ref") or ""),
                str(item.get("manifest_ref") or ""),
            ),
        )
        result_rows = sorted(
            [row.to_dict() for row in self.tool_results],
            key=lambda item: (
                str(item.get("tool_id") or ""),
                str((item.get("run_ref") or {}).get("artifact_ref") or ""),
                str((item.get("timing") or {}).get("started_at") or ""),
            ),
        )
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "case_id": self.case_id,
            "trace_id": self.trace_id,
            "tool_runs": run_rows,
            "tool_results": result_rows,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ToolEvidencePack":
        if not isinstance(payload, dict):
            raise ValueError("tool_evidence_pack payload must be an object")
        raw_runs = payload.get("tool_runs", [])
        raw_results = payload.get("tool_results", [])
        if not isinstance(raw_runs, list):
            raise ValueError("tool_runs must be a list")
        if not isinstance(raw_results, list):
            raise ValueError("tool_results must be a list")

        return cls(
            tool_runs=[ToolRunRef.from_dict(_as_dict("tool_run", item)) for item in raw_runs],
            tool_results=[ToolResult.from_dict(_as_dict("tool_result", item)) for item in raw_results],
            run_id=_optional_str(payload.get("run_id")),
            case_id=_optional_str(payload.get("case_id")),
            trace_id=_optional_str(payload.get("trace_id")),
            schema_version=int(payload.get("schema_version", 1) or 1),
        )

    @classmethod
    def from_json(cls, raw: str) -> "ToolEvidencePack":
        parsed = json.loads(str(raw))
        if not isinstance(parsed, dict):
            raise ValueError("tool_evidence_pack JSON must decode to object")
        return cls.from_dict(parsed)


def validate_tool_contract(schema_name: str, payload: Dict[str, Any]) -> None:
    validator = _validator(schema_name)
    errors = sorted(validator.iter_errors(payload), key=lambda item: str(list(item.path)))
    if not errors:
        return
    err = errors[0]
    location = ".".join(str(part) for part in err.path)
    message = f"{err.message} at '{location}'" if location else err.message
    raise ToolContractValidationError(schema_name=str(schema_name), message=message)


@lru_cache(maxsize=len(_SCHEMA_FILE_BY_NAME))
def _validator(schema_name: str) -> Draft202012Validator:
    schema_path = _SCHEMA_FILE_BY_NAME.get(str(schema_name))
    if schema_path is None:
        raise ToolContractValidationError(schema_name=str(schema_name), message="unknown schema")
    if not schema_path.exists():
        raise ToolContractValidationError(
            schema_name=str(schema_name),
            message=f"schema file missing: {schema_path}",
        )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return Draft202012Validator(schema, registry=_schema_registry())


def _non_empty(name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _string_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for item in values:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _as_dict(name: str, value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    raise ValueError(f"{name} must be an object")


@lru_cache(maxsize=1)
def _schema_registry() -> Registry:
    registry = Registry()
    for path in _SCHEMA_DIR.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(schema)
        registry = registry.with_resource(path.name, resource)
        schema_id = schema.get("$id")
        if isinstance(schema_id, str) and schema_id:
            registry = registry.with_resource(schema_id, resource)
        alias = f"https://example.local/schemas/{path.name}"
        registry = registry.with_resource(alias, resource)
    return registry
