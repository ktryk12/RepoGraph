from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping
import json

from babyai_shared.fingerprint import sha256_json


_ACCEPT_VALUES = {"accept", "accepted", "allow", "allowed", "pass", "passed", "ok", "true", "1"}
_REJECT_VALUES = {"reject", "rejected", "deny", "denied", "fail", "failed", "false", "0"}
_REVIEW_VALUES = {"review", "pending", "needs_review"}


class TrainingJudgeGateViolation(RuntimeError):
    def __init__(self, *, message: str, details: Dict[str, Any]) -> None:
        self.details = dict(details)
        super().__init__(str(message))


@dataclass(frozen=True)
class TrainingJudgeGateVerdict:
    allowed: bool
    decision_counts: Dict[str, int]
    blocked_rows: List[Dict[str, Any]]
    fingerprint: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": bool(self.allowed),
            "decision_counts": {str(k): int(v) for k, v in sorted(self.decision_counts.items())},
            "blocked_rows": [dict(row) for row in self.blocked_rows],
            "fingerprint": str(self.fingerprint),
        }


class TrainingJudgeGateService:
    """
    ACCEPT-only gate for training rows.

    Default behavior treats missing judge decision as ACCEPT for backward compatibility.
    """

    def __init__(self, *, default_decision: str = "accept") -> None:
        self._default_decision = _normalize_decision(default_decision, fallback="accept")

    def evaluate_rows(self, rows: Iterable[Mapping[str, Any] | Dict[str, Any]]) -> TrainingJudgeGateVerdict:
        counts: Dict[str, int] = {"accept": 0, "review": 0, "reject": 0, "unknown": 0}
        blocked: List[Dict[str, Any]] = []
        normalized_rows: List[Dict[str, Any]] = []

        for index, raw in enumerate(rows):
            row = _as_dict(raw)
            decision = self._row_decision(row)
            counts[decision] = int(counts.get(decision, 0)) + 1
            if decision != "accept":
                blocked.append(
                    {
                        "index": int(index),
                        "decision": decision,
                        "case_id": _optional_text(row.get("case_id") or row.get("task_id") or row.get("context_id")),
                    }
                )
            normalized_rows.append({"index": int(index), "decision": decision})

        allowed = len(blocked) == 0
        fingerprint = sha256_json(
            {
                "schema_version": 1,
                "default_decision": self._default_decision,
                "rows": normalized_rows,
                "counts": counts,
            }
        )
        return TrainingJudgeGateVerdict(
            allowed=allowed,
            decision_counts=counts,
            blocked_rows=blocked,
            fingerprint=fingerprint,
        )

    def evaluate_jsonl(self, path: str | Path) -> TrainingJudgeGateVerdict:
        rows = load_jsonl_rows(path)
        return self.evaluate_rows(rows)

    def require_accept_only(self, rows: Iterable[Mapping[str, Any] | Dict[str, Any]]) -> TrainingJudgeGateVerdict:
        verdict = self.evaluate_rows(rows)
        if verdict.allowed:
            return verdict
        preview = ", ".join(
            f"{item.get('decision')}@{item.get('index')}"
            for item in verdict.blocked_rows[:3]
        )
        raise TrainingJudgeGateViolation(
            message=f"training_judge_gate_rejected rows={len(verdict.blocked_rows)} preview={preview}",
            details=verdict.to_dict(),
        )

    def require_accept_only_jsonl(self, path: str | Path) -> TrainingJudgeGateVerdict:
        rows = load_jsonl_rows(path)
        return self.require_accept_only(rows)

    def _row_decision(self, row: Dict[str, Any]) -> str:
        # Support both top-level and nested metadata judge decisions.
        for key in ("training_judge_decision", "judge_decision"):
            value = _optional_text(row.get(key))
            if value:
                return _normalize_decision(value, fallback="unknown")
        metadata = row.get("metadata")
        if isinstance(metadata, Mapping):
            for key in ("training_judge_decision", "judge_decision"):
                value = _optional_text(metadata.get(key))
                if value:
                    return _normalize_decision(value, fallback="unknown")
        judge_pack = row.get("judge_pack")
        if isinstance(judge_pack, Mapping):
            value = _optional_text(judge_pack.get("judge_decision"))
            if value:
                return _normalize_decision(value, fallback="unknown")
        return self._default_decision


def load_jsonl_rows(path: str | Path) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"dataset not found: {p}")
    out: List[Dict[str, Any]] = []
    for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, Mapping):
            out.append(dict(payload))
    return out


def _normalize_decision(value: Any, *, fallback: str) -> str:
    token = str(value or "").strip().lower()
    if token in _ACCEPT_VALUES:
        return "accept"
    if token in _REJECT_VALUES:
        return "reject"
    if token in _REVIEW_VALUES:
        return "review"
    return str(fallback)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}

