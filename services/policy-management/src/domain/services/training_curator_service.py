from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence
import json

from babyai_shared.fingerprint import canonical_json, sha256_json


@dataclass(frozen=True)
class TrainingCurationResult:
    curated_rows: List[Dict[str, Any]]
    dataset_fingerprint: str
    row_fingerprints: List[str]
    total_rows: int
    kept_rows: int
    dropped_duplicates: int
    dropped_hygiene: int
    dropped_reasons: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_rows": int(self.total_rows),
            "kept_rows": int(self.kept_rows),
            "dropped_duplicates": int(self.dropped_duplicates),
            "dropped_hygiene": int(self.dropped_hygiene),
            "dropped_reasons": {str(k): int(v) for k, v in sorted(self.dropped_reasons.items())},
            "dataset_fingerprint": str(self.dataset_fingerprint),
            "row_fingerprints": list(self.row_fingerprints),
        }


class TrainingCuratorService:
    """
    Deterministic training dataset curation.

    - hygiene filtering
    - dedupe by canonical row fingerprint
    - stable row ordering + dataset fingerprint
    """

    def __init__(
        self,
        *,
        required_fields: Sequence[str] | None = None,
        max_row_bytes: int = 64_000,
    ) -> None:
        self._required_fields = tuple(sorted({str(x).strip() for x in (required_fields or []) if str(x).strip()}))
        self._max_row_bytes = max(256, int(max_row_bytes))

    def curate_rows(
        self,
        rows: Iterable[Dict[str, Any] | Mapping[str, Any]],
    ) -> TrainingCurationResult:
        dropped: Dict[str, int] = {}
        seen: set[str] = set()
        indexed: List[tuple[str, Dict[str, Any]]] = []
        total = 0
        duplicate_count = 0
        hygiene_count = 0

        for raw in rows:
            total += 1
            row = _as_dict(raw)
            ok, reason = self._hygiene_ok(row)
            if not ok:
                hygiene_count += 1
                dropped[reason] = int(dropped.get(reason, 0)) + 1
                continue
            normalized = _normalize_row(row)
            fingerprint = sha256_json(normalized)
            if fingerprint in seen:
                duplicate_count += 1
                dropped["duplicate_row"] = int(dropped.get("duplicate_row", 0)) + 1
                continue
            seen.add(fingerprint)
            indexed.append((fingerprint, normalized))

        indexed.sort(key=lambda item: item[0])
        curated_rows = [dict(row) for _, row in indexed]
        row_fingerprints = [fingerprint for fingerprint, _ in indexed]
        dataset_fingerprint = sha256_json(
            {
                "schema_version": 1,
                "required_fields": list(self._required_fields),
                "rows": [{"fingerprint": fp, "row": row} for fp, row in indexed],
            }
        )
        return TrainingCurationResult(
            curated_rows=curated_rows,
            dataset_fingerprint=dataset_fingerprint,
            row_fingerprints=row_fingerprints,
            total_rows=int(total),
            kept_rows=len(curated_rows),
            dropped_duplicates=int(duplicate_count),
            dropped_hygiene=int(hygiene_count),
            dropped_reasons={str(k): int(v) for k, v in dropped.items()},
        )

    def curate_jsonl(
        self,
        path: str | Path,
    ) -> TrainingCurationResult:
        rows = load_jsonl_rows(path)
        return self.curate_rows(rows)

    def _hygiene_ok(self, row: Dict[str, Any]) -> tuple[bool, str]:
        if not isinstance(row, dict):
            return False, "row_not_object"
        raw = canonical_json(row).encode("utf-8")
        if len(raw) > self._max_row_bytes:
            return False, "row_too_large"
        for field in self._required_fields:
            value = row.get(field)
            if value is None:
                return False, f"missing_required:{field}"
            if isinstance(value, str) and not value.strip():
                return False, f"missing_required:{field}"
        return True, "ok"


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


def write_curated_jsonl(path: str | Path, rows: Sequence[Dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = ""
    if rows:
        payload = "\n".join(canonical_json(dict(row)) for row in rows) + "\n"
    p.write_text(payload, encoding="utf-8")


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(canonical_json(row))

