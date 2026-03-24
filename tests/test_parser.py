from dataclasses import dataclass, field
from pathlib import Path

from repograph.indexer import parser as parser_module
from repograph.indexer.schema import AT_LINE, DEFINES, IMPORTS, IN_FILE


@dataclass
class FakeNode:
    type: str
    start_byte: int
    end_byte: int
    start_point: tuple[int, int] = (0, 0)
    named_children: list["FakeNode"] = field(default_factory=list)
    fields: dict[str, "FakeNode"] = field(default_factory=dict)

    def child_by_field_name(self, name: str):
        return self.fields.get(name)


@dataclass
class FakeTree:
    root_node: FakeNode


@dataclass
class FakeParser:
    tree: FakeTree

    def parse(self, source: bytes) -> FakeTree:
        return self.tree


def _span(source: str, snippet: str) -> tuple[int, int]:
    start = source.index(snippet)
    return start, start + len(snippet)


def _build_python_tree(source: str) -> FakeTree:
    import_text = "import os"
    from_text = "from pkg import helper"
    function_text = "def foo():\n    return helper()\n"
    name_text = "foo"

    import_start, import_end = _span(source, import_text)
    from_start, from_end = _span(source, from_text)
    function_start, function_end = _span(source, function_text)
    name_start, name_end = _span(source, name_text)

    function_name = FakeNode(
        type="identifier",
        start_byte=name_start,
        end_byte=name_end,
        start_point=(3, 4),
    )
    function_node = FakeNode(
        type="function_definition",
        start_byte=function_start,
        end_byte=function_end,
        start_point=(3, 0),
        named_children=[function_name],
        fields={"name": function_name},
    )
    root = FakeNode(
        type="module",
        start_byte=0,
        end_byte=len(source),
        named_children=[
            FakeNode("import_statement", import_start, import_end, start_point=(0, 0)),
            FakeNode("import_from_statement", from_start, from_end, start_point=(1, 0)),
            function_node,
        ],
    )
    return FakeTree(root_node=root)


def test_parser_extracts_python_function_definitions(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = "import os\nfrom pkg import helper\n\ndef foo():\n    return helper()\n"
    file_path = repo / "example.py"
    file_path.write_text(source, encoding="utf-8", newline="\n")

    monkeypatch.setattr(
        parser_module,
        "_get_parser",
        lambda language: FakeParser(_build_python_tree(source)),
    )

    triples = parser_module.parse_file(file_path, "python", repo_path=repo)

    assert ("example", DEFINES, "example.foo") in triples
    assert ("example.foo", IN_FILE, "example.py") in triples
    assert ("example.foo", AT_LINE, "4") in triples


def test_parser_extracts_imports_correctly(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    source = "import os\nfrom pkg import helper\n\ndef foo():\n    return helper()\n"
    file_path = repo / "example.py"
    file_path.write_text(source, encoding="utf-8", newline="\n")

    monkeypatch.setattr(
        parser_module,
        "_get_parser",
        lambda language: FakeParser(_build_python_tree(source)),
    )

    triples = parser_module.parse_file(file_path, "python", repo_path=repo)

    assert ("example", IMPORTS, "os") in triples
    assert ("example", IMPORTS, "pkg.helper") in triples


def test_parser_returns_empty_triples_on_parse_error(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    file_path = repo / "broken.py"
    file_path.write_text("def broken(:\n", encoding="utf-8", newline="\n")

    def raise_parse_error(language: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(parser_module, "_get_parser", raise_parse_error)

    triples = parser_module.parse_file(file_path, "python", repo_path=repo)

    assert triples == []
