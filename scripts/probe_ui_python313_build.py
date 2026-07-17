from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


PROBE_DOCKERFILE = """\
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY . /app

RUN python -m pip install --upgrade pip \\
    && python -m pip install --no-cache-dir -e . --no-deps \\
    && python -m pip install --no-cache-dir -r requirements.lock \\
    && python -c "import babyai_shared.ops; import babyai_shared.provenance; import confluent_kafka"
"""


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    with tempfile.NamedTemporaryFile("w", suffix=".dockerfile", delete=False, encoding="utf-8") as tmp:
        tmp.write(PROBE_DOCKERFILE)
        dockerfile_path = Path(tmp.name)
    try:
        cmd = [
            "docker",
            "build",
            "--pull",
            "--file",
            str(dockerfile_path),
            "--tag",
            "babyai-ui-python313-probe",
            str(repo_root),
        ]
        completed = subprocess.run(cmd, check=False)
        return int(completed.returncode)
    finally:
        dockerfile_path.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
