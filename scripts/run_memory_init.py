from __future__ import annotations

import argparse
import json
import os

from babyai.memory.virtual_memory import initialize_memory_schema


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Initialize BabyAI virtual memory SQLite schema.")
    parser.add_argument(
        "--db-path",
        default=os.getenv("BABYAI_MEMORY_DB", "state/babyai_memory.sqlite"),
        help="Path to memory SQLite database.",
    )
    args = parser.parse_args(argv)
    db_path = initialize_memory_schema(args.db_path)
    print(
        json.dumps(
            {
                "initialized": True,
                "db_path": db_path.as_posix(),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
