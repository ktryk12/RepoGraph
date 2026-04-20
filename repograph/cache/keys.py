"""Redis key builders — namespaced per tenant + repo."""

from __future__ import annotations

import hashlib


def _repo_prefix(tenant: str, repo_path: str) -> str:
    repo_hash = hashlib.sha1(repo_path.encode()).hexdigest()[:12]
    return f"repo:{tenant}:{repo_hash}"


def summary_l0(tenant: str, repo_path: str) -> str:
    return f"{_repo_prefix(tenant, repo_path)}:summary:l0"


def summary_service(tenant: str, repo_path: str, service: str) -> str:
    return f"{_repo_prefix(tenant, repo_path)}:service:{service}:summary"


def summary_file(tenant: str, repo_path: str, filepath: str) -> str:
    fhash = hashlib.sha1(filepath.encode()).hexdigest()[:12]
    return f"{_repo_prefix(tenant, repo_path)}:file:{fhash}:summary"


def summary_symbol(tenant: str, repo_path: str, symbol: str) -> str:
    shash = hashlib.sha1(symbol.encode()).hexdigest()[:12]
    return f"{_repo_prefix(tenant, repo_path)}:symbol:{shash}:summary"


def working_set(tenant: str, repo_path: str, query_hash: str) -> str:
    return f"{_repo_prefix(tenant, repo_path)}:workingset:{query_hash}"


def task_state(tenant: str, repo_path: str, task_id: str) -> str:
    return f"{_repo_prefix(tenant, repo_path)}:task:{task_id}:state"


def verify_last(tenant: str, repo_path: str, task_id: str) -> str:
    return f"{_repo_prefix(tenant, repo_path)}:verify:{task_id}:last"


def session_snapshot(tenant: str, session_id: str) -> str:
    return f"session:{tenant}:{session_id}:snapshot"


def query_hash(query: str, profile: str, target_context: int) -> str:
    return hashlib.sha1(f"{query}:{profile}:{target_context}".encode()).hexdigest()[:16]
