from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ast
import posixpath
import sys


FORBIDDEN_ROOTS = {"agents", "bus"}
IMPORT_GUARD_TARGETS = {Path("example_usage.py")}
DEFAULT_TARGETS = (
    "example_usage.py",
    "scripts",
    "verify",
    "aesa",
    "storage",
)
ALLOWED_WRITE_BYPASS_MODULES = {
    Path("verify/artifacts/registry.py"),
    Path("verify/artifacts/writer.py"),
    Path("aesa/infrastructure/artifact_writer_local.py"),
    Path("aesa/infrastructure/artifact_writer_adapters.py"),
    Path("storage/artifact_store.py"),
}
_WRITE_METHODS = {"write_text", "write_bytes"}


@dataclass(frozen=True)
class Violation:
    path: Path
    line: int
    column: int
    kind: str
    module: str
    root: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _iter_python_files(targets: list[str]) -> list[Path]:
    root = _repo_root()
    out: list[Path] = []
    for raw_target in targets:
        candidate = Path(raw_target)
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()

        if candidate.is_file() and candidate.suffix == ".py":
            out.append(candidate)
            continue
        if candidate.is_dir():
            out.extend(sorted(p for p in candidate.rglob("*.py") if p.is_file()))
    return out


def _root_of_module(module: str) -> str:
    return module.split(".", 1)[0].strip()


def _collect_violations(path: Path) -> list[Violation]:
    text = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text, filename=str(path))
    violations: list[Violation] = []
    run_import_guard = _should_run_import_guard(path)
    allow_write_bypass = _is_write_bypass_allowlisted(path)

    for node in ast.walk(tree):
        if run_import_guard and isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                root = _root_of_module(module)
                if root in FORBIDDEN_ROOTS:
                    violations.append(
                        Violation(
                            path=path,
                            line=int(getattr(node, "lineno", 1)),
                            column=int(getattr(node, "col_offset", 0)) + 1,
                            kind="import",
                            module=module,
                            root=root,
                        )
                    )
        elif run_import_guard and isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            module = node.module
            root = _root_of_module(module)
            if root in FORBIDDEN_ROOTS:
                violations.append(
                    Violation(
                        path=path,
                        line=int(getattr(node, "lineno", 1)),
                        column=int(getattr(node, "col_offset", 0)) + 1,
                        kind="from_import",
                        module=module,
                        root=root,
                    )
                )
        elif allow_write_bypass:
            continue
        elif isinstance(node, ast.Call):
            violations.extend(_write_bypass_violations(path, node))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    targets = args or list(DEFAULT_TARGETS)
    files = _iter_python_files(targets)
    root = _repo_root()

    all_violations: list[Violation] = []
    parse_errors: list[tuple[Path, str]] = []
    for path in files:
        try:
            all_violations.extend(_collect_violations(path))
        except (OSError, SyntaxError) as exc:
            parse_errors.append((path, str(exc)))

    if parse_errors:
        print("Failed to parse one or more files:")
        for path, msg in parse_errors:
            rel = path.relative_to(root) if path.is_relative_to(root) else path
            print(f"- {rel}: {msg}")
        return 1

    if all_violations:
        print("Forbidden bypass patterns found:")
        for item in sorted(all_violations, key=lambda v: (str(v.path), v.line, v.column, v.module)):
            rel = item.path.relative_to(root) if item.path.is_relative_to(root) else item.path
            if item.kind in {"import", "from_import"}:
                print(
                    f"- {rel}:{item.line}:{item.column} "
                    f"{item.kind} '{item.module}' (root='{item.root}')"
                )
            else:
                print(
                    f"- {rel}:{item.line}:{item.column} "
                    f"{item.kind} target='{item.module}'"
                )
        print(f"Total violations: {len(all_violations)}")
        return 1

    print(f"No forbidden bypass patterns found in {len(files)} file(s).")
    return 0


def _write_bypass_violations(path: Path, node: ast.Call) -> list[Violation]:
    out: list[Violation] = []
    mode = None
    target_path = None
    kind = ""

    if isinstance(node.func, ast.Name) and node.func.id == "open":
        mode = _open_mode(node=node, mode_arg_index=1)
        target_path = _open_target_path(node)
        kind = "artifact_write_open"
    elif isinstance(node.func, ast.Attribute):
        if node.func.attr == "open":
            mode = _open_mode(node=node, mode_arg_index=0)
            target_path = _path_literal(node.func.value)
            kind = "artifact_write_open"
        elif node.func.attr in _WRITE_METHODS:
            mode = "w"
            target_path = _path_literal(node.func.value)
            kind = "artifact_write_pathlib"

    if not kind or not _is_write_mode(mode) or not _is_protected_write_target(target_path):
        return out
    out.append(
        Violation(
            path=path,
            line=int(getattr(node, "lineno", 1)),
            column=int(getattr(node, "col_offset", 0)) + 1,
            kind=kind,
            module=str(target_path),
            root="write_bypass",
        )
    )
    return out


def _open_target_path(node: ast.Call) -> str | None:
    if node.args:
        return _path_literal(node.args[0])
    for kw in node.keywords:
        if kw.arg in {"file", "path", "name"}:
            return _path_literal(kw.value)
    return None


def _open_mode(*, node: ast.Call, mode_arg_index: int) -> str | None:
    if len(node.args) > mode_arg_index:
        return _string_literal(node.args[mode_arg_index])
    for kw in node.keywords:
        if kw.arg == "mode":
            return _string_literal(kw.value)
    return None


def _is_write_mode(mode: str | None) -> bool:
    if not isinstance(mode, str):
        return False
    value = mode.strip().lower()
    return any(token in value for token in ("w", "a", "x"))


def _string_literal(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return str(node.value)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            parts.append(str(value.value))
        return "".join(parts)
    return None


def _path_literal(node: ast.AST) -> str | None:
    direct = _string_literal(node)
    if direct is not None:
        return direct

    if isinstance(node, ast.Call):
        call_name = _call_name(node.func)
        if call_name in {"Path", "pathlib.Path"} and node.args:
            return _path_literal(node.args[0])
        if call_name == "str" and node.args:
            return _path_literal(node.args[0])
        return None

    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _path_literal(node.left)
        right = _path_literal(node.right)
        if left is None or right is None:
            return None
        return posixpath.join(left.replace("\\", "/"), right.replace("\\", "/"))

    return None


def _call_name(func: ast.AST) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        prefix = _call_name(func.value)
        if prefix:
            return f"{prefix}.{func.attr}"
        return func.attr
    return None


def _is_protected_write_target(raw: str | None) -> bool:
    if not isinstance(raw, str) or not raw.strip():
        return False
    text = raw.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return (
        text == "artifacts"
        or text.startswith("artifacts/")
        or text == "storage"
        or text.startswith("storage/")
    )


def _repo_rel(path: Path) -> Path | None:
    root = _repo_root().resolve()
    target = path.resolve()
    if target.is_relative_to(root):
        return target.relative_to(root)
    return None


def _should_run_import_guard(path: Path) -> bool:
    rel = _repo_rel(path)
    if rel is None:
        return False
    rel_norm = Path(rel.as_posix())
    return any(rel_norm == candidate for candidate in IMPORT_GUARD_TARGETS)


def _is_write_bypass_allowlisted(path: Path) -> bool:
    rel = _repo_rel(path)
    if rel is None:
        return False
    rel_norm = Path(rel.as_posix())
    return any(rel_norm == candidate for candidate in ALLOWED_WRITE_BYPASS_MODULES)


if __name__ == "__main__":
    raise SystemExit(main())
