"""Auto-indexing for RepoGraph.

Keeps a repository's graph in sync with the source, and only re-indexes when the
repo has actually changed. Staleness is detected from a cheap *signature*:

* git repositories  -> the current ``HEAD`` commit
* everything else   -> the newest source-file modification time

Because the check is cheap and the index rebuild is skipped when nothing changed,
this is safe to wire into events that fire "a new repo showed up" or "the repo
moved to a new state" — a clone, a checkout, a merge/pull, or an AI agent
starting a session. See ``docs/AUTO_INDEXING.md`` for the wiring recipes.

Entry points:
    ensure_indexed(repo_path, ...)   importable API, returns a result dict
    main()                            ``repograph-autoindex`` console script
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

STATE_FILENAME = "autoindex_state.json"


# --------------------------------------------------------------------------- #
# Signature helpers
# --------------------------------------------------------------------------- #
def _run_git(repo_path: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _git_signature(repo_path: Path) -> str | None:
    head = _run_git(repo_path, "rev-parse", "HEAD")
    if head:
        return f"git:{head}"
    return None


def _mtime_signature(repo_path: Path) -> str:
    """Fallback signature for non-git repos: newest source-file mtime + count."""
    try:
        from repograph.indexer.walker import walk
    except Exception:  # pragma: no cover - defensive
        return "mtime:unknown"

    newest = 0.0
    count = 0
    for filepath, _language in walk(str(repo_path)):
        try:
            newest = max(newest, Path(filepath).stat().st_mtime)
        except OSError:
            continue
        count += 1
    return f"mtime:{int(newest)}:{count}"


def compute_signature(repo_path: Path) -> str:
    """Return a signature that changes whenever the repo needs re-indexing."""
    return _git_signature(repo_path) or _mtime_signature(repo_path)


# --------------------------------------------------------------------------- #
# State file (sidecar next to the graph store)
# --------------------------------------------------------------------------- #
def _db_path_for_tenant(tenant: str | None) -> Path:
    from repograph.graph.factory import DEFAULT_DB_PATH

    base = os.getenv("REPOGRAPH_DB_PATH", DEFAULT_DB_PATH)
    if tenant:
        base = f"{base}_{tenant}"
    return Path(base)


def _state_path(tenant: str | None) -> Path:
    return _db_path_for_tenant(tenant) / STATE_FILENAME


def _load_state(tenant: str | None) -> dict[str, Any]:
    path = _state_path(tenant)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(tenant: str | None, state: dict[str, Any]) -> None:
    path = _state_path(tenant)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Indexing invocation (in-process or over HTTP)
# --------------------------------------------------------------------------- #
def _index_in_process(repo_path: Path, tenant: str | None) -> dict[str, Any]:
    from repograph.api.routes import IndexRequest, index_repo

    return index_repo(IndexRequest(repo_path=str(repo_path), force=True), x_tenant_id=tenant)


def _index_over_http(repo_path: Path, tenant: str | None, api_url: str) -> dict[str, Any]:
    import httpx

    headers = {"X-Tenant-ID": tenant} if tenant else {}
    resp = httpx.post(
        f"{api_url.rstrip('/')}/index",
        json={"repo_path": str(repo_path), "force": True},
        headers=headers,
        timeout=600,
    )
    resp.raise_for_status()
    return resp.json()


def _is_indexed(tenant: str | None, api_url: str | None) -> bool:
    """True if the store already holds an index for a repo."""
    try:
        if api_url:
            import httpx

            headers = {"X-Tenant-ID": tenant} if tenant else {}
            resp = httpx.get(f"{api_url.rstrip('/')}/status", headers=headers, timeout=30)
            resp.raise_for_status()
            return bool(resp.json().get("indexed"))
        from repograph.api.routes import status

        return bool(status(x_tenant_id=tenant).get("indexed"))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
# Sentinel: distinguishes "tenant not given" (fall back to env) from an explicit
# tenant=None, which callers use to target the base, un-suffixed store.
_TENANT_UNSET: Any = object()


def ensure_indexed(
    repo_path: str | os.PathLike[str] | None = None,
    *,
    tenant: str | None = _TENANT_UNSET,
    force: bool = False,
    api_url: str | None = None,
    check_only: bool = False,
) -> dict[str, Any]:
    """Index ``repo_path`` if its signature changed since the last run.

    Returns a dict with ``action`` in {``indexed``, ``skipped``, ``stale``} plus
    the signatures involved and (when indexed) the raw index result.
    """
    repo_path = Path(repo_path or os.getcwd()).expanduser().resolve()
    if tenant is _TENANT_UNSET:
        tenant = os.getenv("REPOGRAPH_TENANT_ID")
    api_url = api_url or os.getenv("REPOGRAPH_API_URL")

    signature = compute_signature(repo_path)
    state = _load_state(tenant)

    same_repo = state.get("repo_path") == str(repo_path)
    up_to_date = same_repo and state.get("signature") == signature and _is_indexed(tenant, api_url)

    if up_to_date and not force:
        return {
            "action": "skipped",
            "reason": "signature-unchanged",
            "repo_path": str(repo_path),
            "signature": signature,
        }

    if check_only:
        return {
            "action": "stale",
            "repo_path": str(repo_path),
            "signature": signature,
            "previous_signature": state.get("signature"),
        }

    if api_url:
        result = _index_over_http(repo_path, tenant, api_url)
    else:
        result = _index_in_process(repo_path, tenant)

    _save_state(
        tenant,
        {"repo_path": str(repo_path), "signature": signature},
    )

    return {
        "action": "indexed",
        "repo_path": str(repo_path),
        "signature": signature,
        "result": result,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repograph-autoindex",
        description="Index a repository only when it has changed since the last run.",
    )
    parser.add_argument("repo_path", nargs="?", default=None, help="Repo to index (default: cwd)")
    parser.add_argument("--tenant", default=None, help="Tenant id (default: $REPOGRAPH_TENANT_ID)")
    parser.add_argument("--force", action="store_true", help="Re-index even if unchanged")
    parser.add_argument("--api-url", default=None, help="Index via a running API instead of in-process")
    parser.add_argument("--check", action="store_true", help="Report staleness only; do not index (exit 1 if stale)")
    parser.add_argument("--quiet", action="store_true", help="Only print on error")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        result = ensure_indexed(
            args.repo_path,
            # Only pass tenant when given on the CLI, so the env fallback applies.
            **({} if args.tenant is None else {"tenant": args.tenant}),
            force=args.force,
            api_url=args.api_url,
            check_only=args.check,
        )
    except Exception as exc:  # noqa: BLE001 - surface any failure to the caller
        print(f"repograph-autoindex: {exc}", file=sys.stderr)
        return 1

    action = result["action"]
    if action == "stale":
        if not args.quiet:
            print(f"repograph-autoindex: STALE {result['repo_path']} ({result['signature']})")
        return 1
    if not args.quiet:
        if action == "indexed":
            res = result.get("result", {})
            print(
                f"repograph-autoindex: indexed {result['repo_path']} "
                f"({res.get('files_indexed', '?')} files, {res.get('triples_added', '?')} triples)"
            )
        else:
            print(f"repograph-autoindex: up to date ({result['repo_path']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
