"""Parse-time node enrichment: service name, risk level, test detection, entrypoint detection."""

from __future__ import annotations

from pathlib import Path

_SERVICE_ANCHOR_DIRS = {
    "services", "service", "workers", "worker", "jobs", "job",
    "handlers", "handler", "routes", "routers", "models", "repositories",
    "clients", "connectors", "adapters", "core", "domain", "controllers",
    "api", "grpc", "rpc", "events", "tasks", "commands", "queries",
}

_TEST_DIR_NAMES = {"tests", "test", "spec", "specs", "__tests__"}

_ENTRYPOINT_NAMES = {
    "__main__.py", "main.py", "app.py", "server.py", "wsgi.py", "asgi.py",
    "manage.py", "cli.py", "entrypoint.py", "run.py", "start.py",
}


def resolve_service_name(relative_filepath: str) -> str:
    """Infer a service/subsystem name from a relative file path."""
    parts = Path(relative_filepath).parts
    lower_parts = [p.lower() for p in parts]

    if _is_test_file(relative_filepath):
        return "test"

    for i, part in enumerate(lower_parts[:-1]):
        if part in _SERVICE_ANCHOR_DIRS:
            # Use the next directory component as the service name, if it exists
            if i + 1 < len(lower_parts) - 1:
                return lower_parts[i + 1]
            return part

    # Fall back to first non-trivial directory
    for part in lower_parts[:-1]:
        if part not in {"src", "lib", "pkg", ".", ""}:
            return part

    return lower_parts[-1].removesuffix(".py") if lower_parts else "unknown"


def is_entrypoint_file(relative_filepath: str) -> bool:
    """Return True if this file is a recognised application entrypoint."""
    return Path(relative_filepath).name in _ENTRYPOINT_NAMES


def _is_test_file(relative_filepath: str) -> bool:
    parts = [p.lower() for p in Path(relative_filepath).parts]
    filename = parts[-1] if parts else ""
    return (
        filename.startswith("test_")
        or filename.endswith("_test.py")
        or filename.endswith(".spec.ts")
        or filename.endswith(".test.ts")
        or filename.endswith(".spec.js")
        or filename.endswith(".test.js")
        or any(p in _TEST_DIR_NAMES for p in parts[:-1])
    )


def risk_level(symbol: str, is_test: bool, is_entrypoint: bool) -> str:
    """
    Parse-time risk heuristic — caller-count enrichment happens in post-index pass.

    Rules:
    - test symbols → low
    - private symbols (leading _) → low
    - entrypoint symbols → high
    - public symbols → medium
    """
    if is_test:
        return "low"
    short_name = symbol.rsplit(".", 1)[-1]
    if short_name.startswith("__") and short_name.endswith("__"):
        return "medium"  # dunder methods are often called externally
    if short_name.startswith("_"):
        return "low"
    if is_entrypoint:
        return "high"
    return "medium"


def extract_signature(node, source: bytes) -> str | None:
    """Extract a compact signature string from a tree-sitter function/class node."""
    try:
        text = source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")
        # Take only the first line (declaration line)
        first_line = text.split("\n", 1)[0].strip()
        # Truncate long signatures
        return first_line[:200] if first_line else None
    except Exception:
        return None
