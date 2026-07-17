# tools/print_bundle.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]  # repo_root/tools/print_bundle.py

OUT_DIR = REPO_ROOT / "ml" / "bundles"
OUT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_OUT = OUT_DIR / "bundle_for_llm.txt"


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def with_line_numbers(text: str) -> str:
    lines = text.splitlines()
    width = len(str(max(1, len(lines))))
    return "\n".join(f"{i+1:>{width}} | {line}" for i, line in enumerate(lines)) + ("\n" if lines else "")


def header(title: str) -> str:
    bar = "=" * 80
    return f"\n{bar}\n{title}\n{bar}\n"


def file_block(path: Path, content: str) -> str:
    rel = path.relative_to(REPO_ROOT)
    return header(f"FILE: {rel}") + with_line_numbers(content)


def extract_function_block(py_text: str, fn_name: str) -> Optional[str]:
    """
    Extracts a top-level Python function definition block:
      def <fn_name>(...):
          ...
    Best-effort by indentation scanning.
    """
    lines = py_text.splitlines()
    # Find the def line at column 0
    start = None
    pat = re.compile(rf"^def\s+{re.escape(fn_name)}\s*\(")
    for i, line in enumerate(lines):
        if pat.match(line):
            start = i
            break
    if start is None:
        return None

    # Determine block indentation (top-level def => 0)
    # Capture until next top-level "def " or "class " at col 0 (excluding decorators)
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^(def|class)\s+", lines[j]) and not lines[j].startswith(" "):
            end = j
            break

    return "\n".join(lines[start:end]) + "\n"


def extract_region(py_text: str, start_pat: str, end_pat: str) -> Optional[str]:
    """
    Extract inclusive region between regex patterns (first match of each).
    """
    lines = py_text.splitlines()
    s = None
    e = None
    sp = re.compile(start_pat)
    ep = re.compile(end_pat)
    for i, line in enumerate(lines):
        if s is None and sp.search(line):
            s = i
            continue
        if s is not None and ep.search(line):
            e = i
            break
    if s is None:
        return None
    if e is None:
        e = len(lines) - 1
    return "\n".join(lines[s : e + 1]) + "\n"


def maybe_trim_hybrid_generator(path: Path, text: str) -> str:
    """
    For ml/hybrid_generator.py: keep imports + generate_decision only (best effort).
    Falls back to full file if extraction fails.
    """
    # Keep first ~120 lines (imports / constants / helper types) + generate_decision function.
    head_lines = text.splitlines()[:120]
    head = "\n".join(head_lines) + "\n"

    fn = extract_function_block(text, "generate_decision")
    if fn:
        return head + "\n# --- extracted generate_decision ---\n" + fn

    # fallback: try a region from "def generate_decision" to EOF
    region = extract_region(text, r"^def\s+generate_decision\s*\(", r"^\Z")
    if region:
        return head + "\n# --- extracted generate_decision (region fallback) ---\n" + region

    return text  # give up, include full file


def load_one_example(task_path: Path, cases_path: Path) -> Tuple[str, str]:
    """
    Returns (task_json_text, case_json_text) for one representative row.
    - task: first task file if present
    - case: first line from cases.jsonl
    """
    task_text = ""
    case_text = ""

    if task_path.exists():
        task_text = read_text(task_path)

    if cases_path.exists():
        # take first non-empty line
        for line in read_text(cases_path).splitlines():
            line = line.strip()
            if line:
                case_text = line + "\n"
                break

    return task_text, case_text


def resolve_paths() -> dict[str, Path]:
    return {
        "eval_generator": REPO_ROOT / "ml" / "eval_generator.py",
        "analyze_failures": REPO_ROOT / "ml" / "analyze_failures.py",
        "hybrid_generator": REPO_ROOT / "ml" / "hybrid_generator.py",
        "features": REPO_ROOT / "ml" / "features.py",
        "scorer": REPO_ROOT / "policy" / "scorer.py",
        "rules": REPO_ROOT / "policy" / "policy_rules.yaml",
        "must_include": REPO_ROOT / "policy" / "must_include_checks.py",
        "cases": REPO_ROOT / "ml" / "data" / "current_cases.jsonl",
        "tasks_dir": REPO_ROOT / "ml" / "data" / "tasks",
    }


def pick_example_task(tasks_dir: Path) -> Optional[Path]:
    if not tasks_dir.exists():
        return None
    # pick first json file (stable order)
    candidates = sorted([p for p in tasks_dir.rglob("*.json") if p.is_file()])
    return candidates[0] if candidates else None


def bundle(out_path: Path = DEFAULT_OUT) -> None:
    P = resolve_paths()

    parts: list[str] = []
    parts.append(header("BUNDLE FOR LLM REVIEW (curated)"))
    parts.append("This bundle is a curated snapshot of core logic for understanding the system.\n")

    # 1) Eval loop
    if P["eval_generator"].exists():
        parts.append(file_block(P["eval_generator"], read_text(P["eval_generator"])))
    else:
        parts.append(header("MISSING: ml/eval_generator.py"))

    # 2) Policy & scoring
    for key in ["rules", "scorer", "must_include"]:
        path = P[key]
        if path.exists():
            parts.append(file_block(path, read_text(path)))
        else:
            parts.append(header(f"MISSING: {path.relative_to(REPO_ROOT)}"))

    # 3) Generator contract (trimmed)
    hg = P["hybrid_generator"]
    if hg.exists():
        txt = read_text(hg)
        txt = maybe_trim_hybrid_generator(hg, txt)
        parts.append(file_block(hg, txt))
    else:
        parts.append(header("MISSING: ml/hybrid_generator.py"))

    # 4) Features (only if it exists; helps interpret diffs)
    if P["features"].exists():
        parts.append(file_block(P["features"], read_text(P["features"])))

    # 5) One example
    ex_task = pick_example_task(P["tasks_dir"])
    task_text, case_text = ("", "")
    if ex_task:
        task_text = read_text(ex_task)
    if P["cases"].exists():
        # first non-empty line from cases
        for line in read_text(P["cases"]).splitlines():
            if line.strip():
                case_text = line.strip() + "\n"
                break

    parts.append(header("EXAMPLE TASK (first found)"))
    parts.append(with_line_numbers(task_text) if task_text else "(No task found in ml/data/tasks)\n")

    parts.append(header("EXAMPLE CASE (first line from current_cases.jsonl)"))
    parts.append(with_line_numbers(case_text) if case_text else "(No cases found in ml/data/current_cases.jsonl)\n")

    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"✅ Wrote bundle: {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    bundle()
