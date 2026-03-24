from pathlib import Path

from repograph.indexer.walker import walk


def test_walk_finds_python_files_and_languages(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source_file = repo / "main.py"
    source_file.write_text("print('hello')\n", encoding="utf-8")

    results = list(walk(str(repo)))

    assert results == [(source_file.resolve(), "python")]


def test_walk_respects_gitignore_for_node_modules(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    ignored_dir = repo / "node_modules"
    ignored_dir.mkdir()
    (ignored_dir / "ignored.py").write_text("print('ignored')\n", encoding="utf-8")
    kept_file = repo / "app.py"
    kept_file.write_text("print('kept')\n", encoding="utf-8")

    results = list(walk(str(repo)))

    assert results == [(kept_file.resolve(), "python")]


def test_walk_skips_pycache_directories(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    pycache_dir = repo / "__pycache__"
    pycache_dir.mkdir()
    (pycache_dir / "cached.py").write_text("print('cached')\n", encoding="utf-8")
    kept_file = repo / "module.py"
    kept_file.write_text("print('module')\n", encoding="utf-8")

    results = list(walk(str(repo)))

    assert results == [(kept_file.resolve(), "python")]
