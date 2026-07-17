from __future__ import annotations

import os
import subprocess
import sys


def main() -> int:
    os.environ["UPDATE_SNAPSHOTS"] = "1"
    args = sys.argv[1:]
    if not args:
        args = ["-q", "-k", "snapshot", "tests"]
    return subprocess.call([sys.executable, "-m", "pytest", *args])


if __name__ == "__main__":
    raise SystemExit(main())
