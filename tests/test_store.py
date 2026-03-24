from pathlib import Path

from repograph.graph.store import RepoGraph


def test_put_triple_and_callers_of_round_trip(tmp_path: Path) -> None:
    graph = RepoGraph(db_path=str(tmp_path / "graph"))
    graph.clear()
    graph.put_triple("module.caller", "CALLS", "module.callee")

    assert graph.callers_of("module.callee") == ["module.caller"]


def test_blast_radius_traverses_two_hops(tmp_path: Path) -> None:
    graph = RepoGraph(db_path=str(tmp_path / "graph"))
    graph.clear()
    graph.put_triples_batch(
        [
            ("leaf", "CALLS", "target"),
            ("root", "CALLS", "leaf"),
        ]
    )

    assert graph.blast_radius("target", depth=2) == {
        "target": ["leaf"],
        "leaf": ["root"],
    }


def test_search_finds_matching_symbols(tmp_path: Path) -> None:
    graph = RepoGraph(db_path=str(tmp_path / "graph"))
    graph.clear()
    graph.put_triple("package.helper_fn", "IN_FILE", "src/package.py")
    graph.put_triple("package.other", "IN_FILE", "src/package.py")

    results = graph.search("helper", limit=20)

    assert results == ["package.helper_fn"]
