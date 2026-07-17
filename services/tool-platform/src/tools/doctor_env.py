from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _path_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _pip_module_path() -> str:
    try:
        import pip  # type: ignore
    except Exception:
        return "<unavailable>"
    return str(Path(pip.__file__).resolve())


def _is_windows_store_python(executable: Path) -> bool:
    return "windowsapps" in str(executable).lower()


def _in_repo_dot_venv(executable: Path, repo_root: Path) -> bool:
    expected_venv = repo_root / ".venv"
    return _path_inside(executable, expected_venv)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    executable = Path(sys.executable).resolve()
    pip_path = _pip_module_path()

    payload = {
        "python_executable": str(executable),
        "python_version": sys.version.split()[0],
        "pip_module": pip_path,
        "venv_active": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
        "virtual_env": str(os.getenv("VIRTUAL_ENV", "")),
        "sys_path_count": len(sys.path),
        "sys_path_head": [str(Path(p).resolve()) if p else "" for p in sys.path[:5]],
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2))

    errors: list[str] = []
    if _is_windows_store_python(executable):
        errors.append("Detected Windows Store Python (WindowsApps). Install CPython 3.13 and recreate .venv.")
    if not _in_repo_dot_venv(executable, repo_root):
        errors.append("Interpreter is not from this repo's .venv. Activate .venv and rerun.")

    if errors:
        for message in errors:
            print(f"ERROR: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
