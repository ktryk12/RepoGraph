#!/usr/bin/env python3
"""
export_slices.py

Eksporterer udvalgte "sandhed + wiring" filer fra repoet til en mappe + zip.
Matcher det du nævnte:
- aesa/application/use_cases/run_episode.py
- aesa/application/ports/*
- aesa/bootstrap/wiring.py (og/eller alternative wiring-filer)
- artifacts/benchmark/benchmark_latest.json (eller nærmeste match)

Kør:
  python export_slices.py . --out export_bundle
  python export_slices.py . --out export_bundle --redact
  python export_slices.py . --out export_bundle --zip-name babyai_slice.zip
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import shutil
from pathlib import Path
from datetime import datetime

# --- Defaults: det vi typisk vil have med ---
DEFAULT_PATHS = [
    "aesa/application/use_cases/run_episode.py",
    "aesa/application/ports",              # hele mappen
    "aesa/bootstrap/wiring.py",
    "aesa/bootstrap/system_bootstrap.py",  # hvis I bruger den i stedet
    "aesa/bootstrap/bootstrap.py",
]

# Artifact-eksempel fallback liste (vi tager første der findes)
ARTIFACT_CANDIDATES = [
    "artifacts/benchmark/benchmark_latest.json",
    "artifacts/benchmark/benchmark_latest.md",
    "artifacts/benchmark/latest_scoreline.json",
    "artifacts/benchmark/benchmark_latest.jsonl",
    "artifacts/benchmark/per_task_latest.jsonl",
]

# Simple secret-redaction patterns (best effort)
REDACT_PATTERNS = [
    # ENV style: KEY=...
    (re.compile(r"(?im)^(OPENAI_API_KEY|API_KEY|TOKEN|SECRET|PASSWORD)\s*=\s*.+$"), r"\1=<REDACTED>"),
    # JSON style: "key": "value"
    (re.compile(r'(?im)("?(openai_api_key|api_key|token|secret|password)"?\s*:\s*)"[^"]*"'), r'\1"<REDACTED>"'),
    # Bearer tokens in text
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-\._~\+\/]+=*"), "Bearer <REDACTED>"),
]

TEXT_EXTS = {".py", ".md", ".txt", ".yml", ".yaml", ".toml", ".json", ".jsonl", ".ini", ".cfg"}


def parse_args():
    p = argparse.ArgumentParser(description="Export a safe, focused slice of a repo (use case + ports + wiring + artifact).")
    p.add_argument("repo_root", nargs="?", default=".", help="Repo root (default: .)")
    p.add_argument("--out", default="export_bundle", help="Output folder")
    p.add_argument("--zip-name", default=None, help="Zip filename (default: <out>.zip)")
    p.add_argument("--redact", action="store_true", help="Redact obvious secrets in text files (best-effort)")
    p.add_argument("--include", nargs="*", default=None,
                   help="Extra paths/globs to include (relative to repo root), e.g. aesa/application/ports/*.py")
    p.add_argument("--exclude", nargs="*", default=None,
                   help="Exclude globs (relative), e.g. '**/__pycache__/**' '*.png'")
    p.add_argument("--artifact", default=None,
                   help="Explicit artifact path to include (relative). If not set, script will pick first existing candidate.")
    return p.parse_args()


def safe_mkdir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTS


def redact_text(text: str) -> str:
    out = text
    for pat, repl in REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


def copy_file(src: Path, dst: Path, do_redact: bool):
    safe_mkdir(dst.parent)
    if do_redact and is_text_file(src):
        try:
            raw = src.read_text(encoding="utf-8", errors="replace")
            dst.write_text(redact_text(raw), encoding="utf-8")
            return
        except Exception:
            # fallback to binary copy
            pass
    shutil.copy2(src, dst)


def match_any_glob(rel_posix: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel_posix, g) for g in globs)


def expand_inputs(repo_root: Path, items: list[str]) -> list[Path]:
    """
    Udvider både filer, mapper og simple globs. Returnerer konkrete paths.
    """
    out: list[Path] = []
    for item in items:
        # Hvis det indeholder wildcard, prøv glob
        if any(ch in item for ch in ["*", "?", "[", "]"]):
            out.extend(repo_root.glob(item))
        else:
            out.append(repo_root / item)
    return out


def collect_paths(repo_root: Path, base_paths: list[str], extra_include: list[str] | None,
                  exclude_globs: list[str] | None) -> list[Path]:
    exclude_globs = exclude_globs or []
    candidates = expand_inputs(repo_root, base_paths + (extra_include or []))

    collected: list[Path] = []
    seen: set[Path] = set()

    for c in candidates:
        if not c.exists():
            continue

        if c.is_dir():
            for p in c.rglob("*"):
                if p.is_dir():
                    continue
                rel = p.relative_to(repo_root).as_posix()
                if exclude_globs and match_any_glob(rel, exclude_globs):
                    continue
                if p not in seen:
                    collected.append(p)
                    seen.add(p)
        else:
            rel = c.relative_to(repo_root).as_posix()
            if exclude_globs and match_any_glob(rel, exclude_globs):
                continue
            if c not in seen:
                collected.append(c)
                seen.add(c)

    return sorted(collected, key=lambda x: x.as_posix())


def pick_artifact(repo_root: Path, explicit: str | None) -> Path | None:
    if explicit:
        p = repo_root / explicit
        return p if p.exists() else None

    for rel in ARTIFACT_CANDIDATES:
        p = repo_root / rel
        if p.exists():
            return p
    return None


def write_manifest(out_dir: Path, repo_root: Path, files: list[Path], artifact: Path | None):
    manifest = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "repo_root": str(repo_root.resolve()),
        "file_count": len(files) + (1 if artifact else 0),
        "included": [f.relative_to(repo_root).as_posix() for f in files],
        "artifact_included": artifact.relative_to(repo_root).as_posix() if artifact else None,
        "notes": "Bundle contains only selected files for review (use case + ports + wiring + artifact example).",
    }
    (out_dir / "EXPORT_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def make_zip(out_dir: Path, zip_name: str):
    zip_path = out_dir.with_suffix("")  # dummy
    # shutil.make_archive kræver base_name uden .zip
    base_name = str((out_dir.parent / zip_name).with_suffix(""))
    shutil.make_archive(base_name=base_name, format="zip", root_dir=out_dir)
    return str((out_dir.parent / f"{Path(base_name).name}.zip").resolve())


def main():
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    out_dir = Path(args.out).resolve()

    # Standard eksklusioner (støj)
    default_excludes = [
        "**/__pycache__/**",
        "**/.git/**",
        "**/.venv/**",
        "**/venv/**",
        "**/.pytest_cache/**",
        "**/.mypy_cache/**",
        "**/.ruff_cache/**",
        "**/node_modules/**",
        "**/*.png",
        "**/*.jpg",
        "**/*.jpeg",
        "**/*.webp",
        "**/*.mp4",
        "**/*.mov",
    ]
    exclude = (args.exclude or []) + default_excludes

    files = collect_paths(
        repo_root=repo_root,
        base_paths=DEFAULT_PATHS,
        extra_include=args.include,
        exclude_globs=exclude,
    )

    artifact = pick_artifact(repo_root, args.artifact)
    safe_mkdir(out_dir)

    copied = 0
    for f in files:
        rel = f.relative_to(repo_root)
        dst = out_dir / rel
        copy_file(f, dst, do_redact=args.redact)
        copied += 1

    if artifact:
        rel = artifact.relative_to(repo_root)
        dst = out_dir / rel
        copy_file(artifact, dst, do_redact=args.redact)
        copied += 1

    write_manifest(out_dir, repo_root, files, artifact)

    zip_name = args.zip_name or (Path(args.out).name + ".zip")
    zip_path = make_zip(out_dir, zip_name)

    print("✅ Export færdig")
    print(f"  Repo: {repo_root}")
    print(f"  Output folder: {out_dir}")
    print(f"  Filer kopieret: {copied}")
    print(f"  Zip: {zip_path}")
    if artifact:
        print(f"  Artifact: {artifact.relative_to(repo_root).as_posix()}")
    else:
        print("  Artifact: (ingen fundet — angiv evt. --artifact artifacts/benchmark/benchmark_latest.json)")


if __name__ == "__main__":
    main()
