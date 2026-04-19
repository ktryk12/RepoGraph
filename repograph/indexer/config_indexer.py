"""Config file walker — emits CONFIGURES triples from config nodes to their co-located modules."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from .schema import CONFIGURES, IN_FILE

CONFIG_EXTENSIONS = {
    ".yaml", ".yml", ".toml", ".json", ".env", ".ini", ".cfg",
    ".conf", ".config", ".properties", ".xml",
}

CONFIG_FILENAMES = {
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env", ".env.example", ".env.local",
    "Makefile", "pyproject.toml", "setup.cfg", "setup.py",
    "package.json", "tsconfig.json", "jest.config.js",
    "webpack.config.js", "vite.config.ts",
}

Triple = tuple[str, str, str]


def walk_config_files(repo_path: str) -> Iterator[tuple[Path, str]]:
    """Yield (config_file_path, config_type) for config files in repo."""
    repo_root = Path(repo_path).expanduser().resolve()
    skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv"}

    for current_root, dirs, files in os.walk(repo_root, topdown=True):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for filename in files:
            file_path = Path(current_root) / filename
            if _is_config_file(file_path):
                config_type = _classify_config(file_path)
                yield file_path, config_type


def index_config_file(file_path: Path, repo_root: Path) -> list[Triple]:
    """Emit triples for a single config file."""
    try:
        relative = file_path.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        relative = file_path.name

    config_node = f"config:{relative}"
    triples: list[Triple] = [
        (config_node, IN_FILE, relative),
        (config_node, "config_type", _classify_config(file_path)),
    ]

    # Emit CONFIGURES edge to the nearest Python/JS package in the same directory
    package = _nearest_package(file_path, repo_root)
    if package:
        triples.append((config_node, CONFIGURES, package))

    return triples


def _is_config_file(path: Path) -> bool:
    return path.suffix.lower() in CONFIG_EXTENSIONS or path.name in CONFIG_FILENAMES


def _classify_config(path: Path) -> str:
    name = path.name.lower()
    if "docker" in name:
        return "docker"
    if name in {"pyproject.toml", "setup.cfg", "setup.py"}:
        return "python_package"
    if name in {"package.json", "tsconfig.json"}:
        return "node_package"
    if path.suffix in {".env", ".env.example", ".env.local"} or name.startswith(".env"):
        return "env"
    if path.suffix in {".yaml", ".yml"}:
        return "yaml"
    if path.suffix == ".toml":
        return "toml"
    if path.suffix in {".ini", ".cfg", ".conf", ".config"}:
        return "ini"
    return "config"


def _nearest_package(config_path: Path, repo_root: Path) -> str | None:
    """Find the nearest Python __init__.py or package module in the same directory."""
    directory = config_path.parent
    try:
        rel_dir = directory.resolve().relative_to(repo_root).as_posix()
    except ValueError:
        return None

    if (directory / "__init__.py").exists():
        return rel_dir.replace("/", ".")

    # Fall back to directory name as module hint
    if rel_dir and rel_dir != ".":
        return rel_dir.split("/")[0]
    return None
