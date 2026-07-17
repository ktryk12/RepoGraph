from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence
import re
import subprocess

from babyai_shared.fingerprint import sha256_json
from babyai_shared.review.rules.license_risks import DEFAULT_SPDX_ALLOWLIST
from babyai_shared.storage.safe_paths import safe_segment


_PINNED_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_SPDX_RE = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.+-]+)")

_SECRET_PATTERNS: Sequence[tuple[str, re.Pattern[str]]] = (
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bghp_[A-Za-z0-9]{36,}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    (
        "generic_secret_assignment",
        re.compile(r"(?i)\b(?:api[_-]?key|secret|token)\b\s*[:=]\s*['\"][^'\"\n]{8,}['\"]"),
    ),
)


@dataclass(frozen=True)
class GitIngestFinding:
    tag: str
    message: str
    evidence_ref: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tag": str(self.tag),
            "message": str(self.message),
            "evidence_ref": self.evidence_ref,
        }


@dataclass(frozen=True)
class GitIngestResult:
    ok: bool
    repo_path: str
    source_ref: str
    commit_hash: str
    manifest_path: str
    status: str
    spdx_ids: List[str]
    spdx_findings: List[GitIngestFinding]
    secret_findings: List[GitIngestFinding]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "repo_path": str(self.repo_path),
            "source_ref": str(self.source_ref),
            "commit_hash": str(self.commit_hash),
            "manifest_path": str(self.manifest_path),
            "status": str(self.status),
            "spdx_ids": list(self.spdx_ids),
            "spdx_findings": [item.to_dict() for item in self.spdx_findings],
            "secret_findings": [item.to_dict() for item in self.secret_findings],
        }


class GitIngestService:
    """
    Git ingest with pinned commit, SPDX guard, secrets scan, and manifest artifact.
    """

    def __init__(
        self,
        *,
        artifact_root: str | Path = Path("artifacts") / "ingest" / "git",
        spdx_allowlist: Iterable[str] | None = None,
        max_file_bytes: int = 200_000,
    ) -> None:
        self._artifact_root = Path(artifact_root)
        self._spdx_allowlist = _normalize_allowlist(spdx_allowlist)
        self._max_file_bytes = max(1, int(max_file_bytes))

    def ingest(
        self,
        *,
        repo_path: str | Path,
        commit_hash: str,
        source_ref: str | None = None,
    ) -> GitIngestResult:
        from verify.artifacts.registry import write_artifact
        repo = Path(repo_path).resolve()
        if not repo.exists():
            raise FileNotFoundError(f"repo_path not found: {repo.as_posix()}")
        pinned = _required_text(commit_hash, name="commit_hash")
        if not _PINNED_COMMIT_RE.match(pinned):
            raise ValueError("commit_hash must be a pinned git hash (7-40 hex chars)")
        resolved_commit = _resolve_commit(repo, pinned)
        files = _list_files(repo, resolved_commit)
        scans = self._scan_repo_at_commit(repo=repo, commit_hash=resolved_commit, files=files)

        spdx_ids = scans["spdx_ids"]
        spdx_findings = scans["spdx_findings"]
        secret_findings = scans["secret_findings"]
        ok = (len(spdx_findings) == 0) and (len(secret_findings) == 0)
        status = "passed" if ok else "blocked"
        effective_source_ref = _optional_text(source_ref) or f"git://{repo.name}@{resolved_commit}"

        manifest = {
            "schema_version": 1,
            "service": "GitIngestService",
            "repo_path": repo.as_posix(),
            "source_ref": effective_source_ref,
            "commit_hash": resolved_commit,
            "status": status,
            "scanned_file_count": len(files),
            "manifest": [{"path": path} for path in files],
            "spdx": {
                "allowlist": list(self._spdx_allowlist),
                "identifiers": list(spdx_ids),
                "violations": [item.to_dict() for item in spdx_findings],
            },
            "secrets_scan": {
                "patterns": [name for name, _ in _SECRET_PATTERNS],
                "violations": [item.to_dict() for item in secret_findings],
            },
        }
        manifest["manifest_fingerprint"] = sha256_json(manifest)
        manifest_path = (
            self._artifact_root
            / safe_segment(repo.name or "repo")
            / safe_segment(resolved_commit)
            / "manifest.json"
        )
        write_artifact(
            "git_ingest_manifest_json",
            manifest,
            manifest_path,
            metadata={
                "source_ref": effective_source_ref,
                "job_id": resolved_commit,
            },
        )
        return GitIngestResult(
            ok=ok,
            repo_path=repo.as_posix(),
            source_ref=effective_source_ref,
            commit_hash=resolved_commit,
            manifest_path=manifest_path.as_posix(),
            status=status,
            spdx_ids=list(spdx_ids),
            spdx_findings=spdx_findings,
            secret_findings=secret_findings,
        )

    def _scan_repo_at_commit(
        self,
        *,
        repo: Path,
        commit_hash: str,
        files: Sequence[str],
    ) -> Dict[str, Any]:
        spdx_ids_seen: set[str] = set()
        spdx_findings: List[GitIngestFinding] = []
        secret_findings: List[GitIngestFinding] = []

        for rel_path in files:
            content = _read_text_at_commit(
                repo=repo,
                commit_hash=commit_hash,
                rel_path=rel_path,
                max_bytes=self._max_file_bytes,
            )
            if content is None:
                continue
            for match in _SPDX_RE.finditer(content):
                spdx_id = str(match.group(1) or "").strip()
                if not spdx_id:
                    continue
                spdx_ids_seen.add(spdx_id)
                if spdx_id not in self._spdx_allowlist:
                    spdx_findings.append(
                        GitIngestFinding(
                            tag="spdx_disallowed",
                            message=f"disallowed SPDX identifier '{spdx_id}'",
                            evidence_ref=rel_path,
                        )
                    )

            for tag, pattern in _SECRET_PATTERNS:
                if pattern.search(content):
                    secret_findings.append(
                        GitIngestFinding(
                            tag=f"secret_{tag}",
                            message=f"potential secret detected via pattern '{tag}'",
                            evidence_ref=rel_path,
                        )
                    )

        ordered_spdx = sorted(spdx_ids_seen)
        ordered_spdx_findings = sorted(
            _dedupe_findings(spdx_findings),
            key=lambda item: (str(item.evidence_ref or ""), str(item.tag), str(item.message)),
        )
        ordered_secret_findings = sorted(
            _dedupe_findings(secret_findings),
            key=lambda item: (str(item.evidence_ref or ""), str(item.tag), str(item.message)),
        )
        return {
            "spdx_ids": ordered_spdx,
            "spdx_findings": ordered_spdx_findings,
            "secret_findings": ordered_secret_findings,
        }


def _normalize_allowlist(value: Iterable[str] | None) -> List[str]:
    source = list(value) if value is not None else list(DEFAULT_SPDX_ALLOWLIST)
    out: List[str] = []
    seen = set()
    for raw in source:
        token = str(raw or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    out.sort()
    return out


def _dedupe_findings(values: Sequence[GitIngestFinding]) -> List[GitIngestFinding]:
    out: List[GitIngestFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for item in values:
        key = (str(item.tag), str(item.message), str(item.evidence_ref or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _resolve_commit(repo: Path, commit_hash: str) -> str:
    row = _run_git(repo=repo, args=["rev-parse", f"{commit_hash}^{{commit}}"])
    resolved = row.strip().lower()
    if not _PINNED_COMMIT_RE.match(resolved):
        raise ValueError(f"unable to resolve pinned commit hash: {commit_hash}")
    return resolved


def _list_files(repo: Path, commit_hash: str) -> List[str]:
    raw = _run_git(repo=repo, args=["ls-tree", "-r", "--name-only", commit_hash])
    rows = [str(line).strip() for line in raw.splitlines() if str(line).strip()]
    rows.sort()
    return rows


def _read_text_at_commit(
    *,
    repo: Path,
    commit_hash: str,
    rel_path: str,
    max_bytes: int,
) -> str | None:
    spec = f"{commit_hash}:{Path(rel_path).as_posix()}"
    proc = subprocess.run(
        ["git", "show", spec],
        cwd=repo.as_posix(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return None
    blob = bytes(proc.stdout or b"")
    if not blob:
        return ""
    clipped = blob[: int(max_bytes)]
    if b"\x00" in clipped:
        return None
    return clipped.decode("utf-8", errors="replace")


def _run_git(*, repo: Path, args: Sequence[str]) -> str:
    proc = subprocess.run(
        ["git", *list(args)],
        cwd=repo.as_posix(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"git command failed ({' '.join(args)}): {stderr.strip()}")
    return proc.stdout.decode("utf-8", errors="replace")


def _required_text(value: Any, *, name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"{name} must be non-empty")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None

