"""FastAPI application entrypoint for RepoGraph."""

from __future__ import annotations

import os

import uvicorn

from .routes import app


def main() -> None:
    host = os.getenv("REPOGRAPH_HOST", "127.0.0.1")
    port = int(os.getenv("REPOGRAPH_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
