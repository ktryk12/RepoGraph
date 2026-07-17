from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator


def main() -> int:
    p = argparse.ArgumentParser(description="Validate coding tasks JSONL against schema.")
    p.add_argument(
        "--file",
        default="eval/coding/tasks_mvp.jsonl",
        help="Path to coding tasks JSONL.",
    )
    args = p.parse_args()

    schema_path = Path("schemas/coding_task.schema.json")
    tasks_path = Path(args.file)

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    errors = 0
    lines = [ln for ln in tasks_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for idx, line in enumerate(lines, start=1):
        try:
            obj = json.loads(line)
            validator.validate(obj)
        except Exception as exc:
            errors += 1
            print(f"[coding-tasks] invalid line {idx}: {exc}")

    if errors:
        print(f"[coding-tasks] FAIL ({errors} invalid rows)")
        return 1

    print(f"[coding-tasks] OK ({len(lines)} tasks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
