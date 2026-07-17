from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from ml.learning_events import (
    LearningEvent,
    LearningMinerConfig,
    build_source_manifest,
    compute_dataset_fingerprint,
    parse_learning_event,
)


DEFAULT_ALLOWED_EVENT_TYPES = (
    "eval_result",
    "final_outcome",
    "repair_attempt",
    "repair_proposed",
    "validation_failure",
    "failure",
)


def main() -> int:
    args = _parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jsonl_paths = [Path(p) for p in args.jsonl]
    config = LearningMinerConfig(
        allowed_event_types=tuple(_parse_event_types(args.allowed_event_types)),
        max_event_bytes=int(args.max_event_bytes),
        max_refs=int(args.max_refs),
        min_ref_count=int(args.min_ref_count),
    )

    rows, source_manifests = _load_rows_with_source(jsonl_paths)
    events, dropped_by_reason = _mine(rows, config=config)
    events = sorted(events, key=lambda item: (item.event_id, item.source_ref))
    dataset_fingerprint = compute_dataset_fingerprint(events, config=config)

    out_jsonl = out_dir / "train.jsonl"
    stats_json = out_dir / "stats.json"

    _write_events_jsonl(out_jsonl, events)
    stats = _build_stats(
        events=events,
        dropped_by_reason=dropped_by_reason,
        source_manifests=source_manifests,
        config=config,
        dataset_fingerprint=dataset_fingerprint,
    )
    _write_json(stats_json, stats)

    if args.print_summary:
        print(
            f"[mine-learning] kept={len(events)} dropped={sum(dropped_by_reason.values())} "
            f"dataset_fingerprint={dataset_fingerprint}"
        )
        print(f"[mine-learning] train_jsonl={out_jsonl}")
        print(f"[mine-learning] stats_json={stats_json}")

    return 0


def _mine(
    rows: List[Dict[str, Any]],
    *,
    config: LearningMinerConfig,
) -> tuple[List[LearningEvent], Counter]:
    events: List[LearningEvent] = []
    dropped_by_reason: Counter = Counter()
    seen_event_ids = set()

    for row in rows:
        source_ref = str(row.get("_source_ref") or "")
        source_fingerprint = str(row.get("_source_fingerprint") or "")
        payload = row.get("payload")
        if not isinstance(payload, dict):
            dropped_by_reason["invalid_payload"] += 1
            continue

        event, reason = parse_learning_event(
            payload,
            source_ref=source_ref,
            source_fingerprint=source_fingerprint,
            config=config,
        )
        if event is None:
            dropped_by_reason[str(reason or "dropped_unknown")] += 1
            continue
        if event.event_id in seen_event_ids:
            dropped_by_reason["duplicate_event_id"] += 1
            continue
        seen_event_ids.add(event.event_id)
        events.append(event)

    return events, dropped_by_reason


def _build_stats(
    *,
    events: List[LearningEvent],
    dropped_by_reason: Counter,
    source_manifests: List[Dict[str, Any]],
    config: LearningMinerConfig,
    dataset_fingerprint: str,
) -> Dict[str, Any]:
    event_type_counts: Counter = Counter()
    outcome_counts: Counter = Counter()
    for event in events:
        event_type_counts[event.event_type] += 1
        outcome_counts[event.outcome] += 1

    included = [
        {
            "event_id": event.event_id,
            "source_ref": event.source_ref,
            "source_fingerprint": event.source_fingerprint,
            "event_type": event.event_type,
            "outcome": event.outcome,
            "payload_fingerprint": event.payload_fingerprint,
        }
        for event in events
    ]

    return {
        "schema_version": 1,
        "dataset_fingerprint": str(dataset_fingerprint),
        "total_input_rows": int(len(events) + sum(dropped_by_reason.values())),
        "kept_rows": int(len(events)),
        "dropped_rows": int(sum(dropped_by_reason.values())),
        "dropped_by_reason": {
            str(key): int(value)
            for key, value in sorted(dropped_by_reason.items(), key=lambda item: item[0])
        },
        "event_type_distribution": {
            str(key): int(value)
            for key, value in sorted(event_type_counts.items(), key=lambda item: item[0])
        },
        "outcome_distribution": {
            str(key): int(value)
            for key, value in sorted(outcome_counts.items(), key=lambda item: item[0])
        },
        "source_manifests": list(source_manifests),
        "quality_filters": config.to_dict(),
        "included_events": included,
    }


def _load_rows_with_source(paths: List[Path]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    rows: List[Dict[str, Any]] = []
    source_manifests: List[Dict[str, Any]] = []

    for path in sorted(paths, key=lambda item: item.as_posix()):
        if not path.exists():
            raise FileNotFoundError(f"jsonl not found: {path}")
        source = build_source_manifest(path)
        source_manifests.append(source)
        source_fingerprint = str(source.get("sha256") or "")
        with path.open("r", encoding="utf-8") as handle:
            for line_no, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    rows.append(
                        {
                            "_source_ref": f"{path.as_posix()}:{line_no}",
                            "_source_fingerprint": source_fingerprint,
                            "payload": None,
                        }
                    )
                    continue
                if not isinstance(payload, dict):
                    payload = {"value": payload}
                rows.append(
                    {
                        "_source_ref": f"{path.as_posix()}:{line_no}",
                        "_source_fingerprint": source_fingerprint,
                        "payload": payload,
                    }
                )
    return rows, source_manifests


def _write_events_jsonl(path: Path, events: List[LearningEvent]) -> None:
    rows = [event.to_dict() for event in events]
    payload = ""
    if rows:
        payload = "\n".join(json.dumps(row, ensure_ascii=True, sort_keys=True) for row in rows) + "\n"
    path.write_text(payload, encoding="utf-8")


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parse_event_types(raw: str) -> List[str]:
    out: List[str] = []
    for item in str(raw).split(","):
        token = item.strip().lower()
        if token:
            out.append(token)
    if out:
        return sorted(set(out))
    return sorted(set(DEFAULT_ALLOWED_EVENT_TYPES))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mine deterministic learning events from telemetry JSONL.")
    parser.add_argument(
        "--jsonl",
        nargs="+",
        default=["logs/failures.jsonl"],
        help="One or more telemetry JSONL files.",
    )
    parser.add_argument(
        "--out-dir",
        default="artifacts/learning",
        help="Output directory for train.jsonl and stats.json.",
    )
    parser.add_argument(
        "--allowed-event-types",
        default=",".join(DEFAULT_ALLOWED_EVENT_TYPES),
        help="Comma-separated allow-list of event_type values.",
    )
    parser.add_argument(
        "--max-event-bytes",
        type=int,
        default=16_384,
        help="Drop events whose canonical JSON exceeds this size.",
    )
    parser.add_argument(
        "--max-refs",
        type=int,
        default=64,
        help="Maximum number of artifact/tool refs to retain per event.",
    )
    parser.add_argument(
        "--min-ref-count",
        type=int,
        default=1,
        help="Drop events with fewer than this number of references.",
    )
    parser.add_argument(
        "--print-summary",
        action="store_true",
        help="Print a compact summary to stdout.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
