"""Tree-sitter based static analysis that emits graph triples."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from .schema import AT_LINE, CALLS, DEFINES, IMPORTS, IN_FILE, INHERITS

LOGGER = logging.getLogger(__name__)

Triple = tuple[str, str, str]


@dataclass(frozen=True)
class LanguageProfile:
    function_types: frozenset[str]
    class_types: frozenset[str]
    import_types: frozenset[str]
    call_types: frozenset[str]
    call_target_fields: tuple[str, ...]


@dataclass
class CallableOwner:
    symbol: str
    node: Any
    class_symbol: str | None


@dataclass
class ParseState:
    module_name: str
    relative_file: str
    triples: list[Triple] = field(default_factory=list)
    _seen: set[Triple] = field(default_factory=set)
    import_aliases: dict[str, str] = field(default_factory=dict)
    symbols_by_short_name: dict[str, list[str]] = field(default_factory=dict)
    class_methods: dict[str, dict[str, str]] = field(default_factory=dict)
    callables: list[CallableOwner] = field(default_factory=list)

    def add_triple(self, triple: Triple) -> None:
        if triple not in self._seen:
            self._seen.add(triple)
            self.triples.append(triple)

    def index_symbol(self, short_name: str, symbol: str) -> None:
        self.symbols_by_short_name.setdefault(short_name, []).append(symbol)


PROFILES = {
    "python": LanguageProfile(
        function_types=frozenset({"function_definition"}),
        class_types=frozenset({"class_definition"}),
        import_types=frozenset({"import_statement", "import_from_statement"}),
        call_types=frozenset({"call"}),
        call_target_fields=("function",),
    ),
    "typescript": LanguageProfile(
        function_types=frozenset(
            {"function_declaration", "method_definition", "generator_function_declaration"}
        ),
        class_types=frozenset({"class_declaration"}),
        import_types=frozenset({"import_statement"}),
        call_types=frozenset({"call_expression", "new_expression"}),
        call_target_fields=("function", "constructor", "callee", "name"),
    ),
    "javascript": LanguageProfile(
        function_types=frozenset(
            {"function_declaration", "method_definition", "generator_function_declaration"}
        ),
        class_types=frozenset({"class_declaration"}),
        import_types=frozenset({"import_statement"}),
        call_types=frozenset({"call_expression", "new_expression"}),
        call_target_fields=("function", "constructor", "callee", "name"),
    ),
    "go": LanguageProfile(
        function_types=frozenset({"function_declaration", "method_declaration"}),
        class_types=frozenset({"type_spec"}),
        import_types=frozenset({"import_declaration"}),
        call_types=frozenset({"call_expression"}),
        call_target_fields=("function", "name"),
    ),
    "rust": LanguageProfile(
        function_types=frozenset({"function_item"}),
        class_types=frozenset({"struct_item", "enum_item", "trait_item"}),
        import_types=frozenset({"use_declaration"}),
        call_types=frozenset({"call_expression"}),
        call_target_fields=("function", "value", "name"),
    ),
    "java": LanguageProfile(
        function_types=frozenset({"method_declaration", "constructor_declaration"}),
        class_types=frozenset({"class_declaration", "interface_declaration"}),
        import_types=frozenset({"import_declaration"}),
        call_types=frozenset({"method_invocation", "object_creation_expression"}),
        call_target_fields=("object", "name", "type", "constructor"),
    ),
    "cpp": LanguageProfile(
        function_types=frozenset({"function_definition"}),
        class_types=frozenset({"class_specifier", "struct_specifier"}),
        import_types=frozenset({"preproc_include"}),
        call_types=frozenset({"call_expression"}),
        call_target_fields=("function", "name"),
    ),
    "c": LanguageProfile(
        function_types=frozenset({"function_definition"}),
        class_types=frozenset(),
        import_types=frozenset({"preproc_include"}),
        call_types=frozenset({"call_expression"}),
        call_target_fields=("function", "name"),
    ),
    "c_sharp": LanguageProfile(
        function_types=frozenset({"method_declaration", "constructor_declaration"}),
        class_types=frozenset({"class_declaration", "interface_declaration"}),
        import_types=frozenset({"using_directive"}),
        call_types=frozenset({"invocation_expression", "object_creation_expression"}),
        call_target_fields=("expression", "name", "type"),
    ),
    "ruby": LanguageProfile(
        function_types=frozenset({"method"}),
        class_types=frozenset({"class"}),
        import_types=frozenset({"call", "command", "command_call"}),
        call_types=frozenset({"call", "command", "command_call"}),
        call_target_fields=("method", "name", "receiver"),
    ),
}


def parse_file(path: str | Path, language: str, repo_path: str | Path | None = None) -> list[Triple]:
    """Parse a source file into graph triples."""
    file_path = Path(path).expanduser().resolve()
    repo_root = Path(repo_path).expanduser().resolve() if repo_path else file_path.parent
    module_name = _module_name(file_path, repo_root)
    relative_file = _relative_file(file_path, repo_root)

    profile = PROFILES.get(language)
    if profile is None:
        LOGGER.warning("Unsupported language for parsing: %s (%s)", language, file_path)
        return []

    try:
        source = file_path.read_bytes()
        parser = _get_parser(language)
        tree = parser.parse(source)
        state = ParseState(module_name=module_name, relative_file=relative_file)
        state.add_triple((module_name, IN_FILE, relative_file))
        _collect_nodes(tree.root_node, source, state, profile, owner_symbol=module_name, class_symbol=None)
        _collect_callable_edges(source, state, profile)
        return state.triples
    except Exception as exc:
        LOGGER.warning("Failed to parse %s: %s", file_path, exc)
        return []


@lru_cache(maxsize=None)
def _get_parser(language: str):
    try:
        from tree_sitter_languages import get_parser
    except ModuleNotFoundError:
        try:
            from tree_sitter_language_pack import get_parser
        except ModuleNotFoundError as exc:
            raise ModuleNotFoundError(
                "Parsing requires `tree-sitter-languages` or `tree-sitter-language-pack`."
            ) from exc
    return get_parser(language)


def _collect_nodes(
    node: Any,
    source: bytes,
    state: ParseState,
    profile: LanguageProfile,
    owner_symbol: str,
    class_symbol: str | None,
) -> None:
    if node.type in profile.import_types:
        for import_name, aliases in _extract_imports(node, source, state.module_name, profile):
            state.add_triple((state.module_name, IMPORTS, import_name))
            state.import_aliases.update(aliases)
        if node.type in {"call", "command", "command_call"}:
            return

    if _is_class_node(node, source, profile):
        class_name = _extract_definition_name(node, source)
        if class_name:
            symbol = f"{owner_symbol}.{class_name}" if owner_symbol else class_name
            _index_definition(state, owner_symbol, symbol, class_name, node)
            for base_name in _extract_inheritance(node, source):
                state.add_triple((symbol, INHERITS, _resolve_reference(base_name, state, class_symbol)))
            for child in node.named_children:
                _collect_nodes(child, source, state, profile, owner_symbol=symbol, class_symbol=symbol)
            return

    if node.type in profile.function_types:
        function_name = _extract_definition_name(node, source)
        if function_name:
            symbol = f"{owner_symbol}.{function_name}" if owner_symbol else function_name
            _index_definition(state, owner_symbol, symbol, function_name, node)
            if class_symbol:
                state.class_methods.setdefault(class_symbol, {})[function_name] = symbol
            state.callables.append(CallableOwner(symbol=symbol, node=node, class_symbol=class_symbol))
            for child in node.named_children:
                _collect_nodes(child, source, state, profile, owner_symbol=symbol, class_symbol=class_symbol)
            return

    for child in node.named_children:
        _collect_nodes(child, source, state, profile, owner_symbol=owner_symbol, class_symbol=class_symbol)


def _collect_callable_edges(source: bytes, state: ParseState, profile: LanguageProfile) -> None:
    for callable_owner in state.callables:
        for child in callable_owner.node.named_children:
            _walk_calls(child, source, state, profile, callable_owner)


def _walk_calls(
    node: Any,
    source: bytes,
    state: ParseState,
    profile: LanguageProfile,
    callable_owner: CallableOwner,
) -> None:
    if _is_class_node(node, source, profile) or node.type in profile.function_types:
        return

    if node.type in profile.call_types:
        raw_target = _extract_call_target(node, source, profile)
        if raw_target:
            resolved_target = _resolve_call(raw_target, state, callable_owner)
            state.add_triple((callable_owner.symbol, CALLS, resolved_target))

    for child in node.named_children:
        _walk_calls(child, source, state, profile, callable_owner)


def _index_definition(state: ParseState, owner_symbol: str, symbol: str, short_name: str, node: Any) -> None:
    state.index_symbol(short_name, symbol)
    state.add_triple((owner_symbol, DEFINES, symbol))
    state.add_triple((symbol, IN_FILE, state.relative_file))
    state.add_triple((symbol, AT_LINE, str(node.start_point[0] + 1)))


def _extract_definition_name(node: Any, source: bytes) -> str | None:
    for field_name in ("name", "declarator"):
        child = node.child_by_field_name(field_name)
        if child is not None:
            identifier = _find_identifier(child)
            if identifier is not None:
                return _text(identifier, source)

    identifier = _find_identifier(node)
    if identifier is not None:
        return _text(identifier, source)
    return None


def _find_identifier(node: Any) -> Any | None:
    identifier_types = {
        "identifier",
        "type_identifier",
        "property_identifier",
        "field_identifier",
        "constant",
        "constant_identifier",
        "name",
    }
    if node.type in identifier_types:
        return node
    for child in node.named_children:
        identifier = _find_identifier(child)
        if identifier is not None:
            return identifier
    return None


def _is_class_node(node: Any, source: bytes, profile: LanguageProfile) -> bool:
    if node.type not in profile.class_types:
        return False
    if node.type == "type_spec":
        return any(child.type in {"struct_type", "interface_type"} for child in node.named_children)
    return True


def _extract_imports(
    node: Any, source: bytes, module_name: str, profile: LanguageProfile
) -> list[tuple[str, dict[str, str]]]:
    node_text = _text(node, source)
    if "import_from_statement" in profile.import_types and node.type in {
        "import_statement",
        "import_from_statement",
    }:
        return _parse_python_imports(node_text)
    if node.type == "import_statement":
        return _parse_js_imports(node_text)
    if node.type == "import_declaration":
        return _parse_block_imports(node_text, "go") + _parse_single_import(node_text, "java")
    if node.type == "use_declaration":
        return _parse_rust_imports(node_text)
    if node.type == "preproc_include":
        return _parse_c_family_include(node_text)
    if node.type == "using_directive":
        return _parse_csharp_using(node_text)
    if node.type in {"call", "command", "command_call"}:
        return _parse_ruby_require(node_text, module_name)
    return []


def _parse_python_imports(text: str) -> list[tuple[str, dict[str, str]]]:
    text = " ".join(text.split())
    results: list[tuple[str, dict[str, str]]] = []

    import_match = re.match(r"import\s+(.+)", text)
    if import_match:
        for part in import_match.group(1).split(","):
            token = part.strip()
            if not token:
                continue
            name, alias = _split_alias(token, separator=" as ")
            results.append((name, {alias or name.split(".")[-1]: name}))
        return results

    from_match = re.match(r"from\s+([.\w]+)\s+import\s+(.+)", text)
    if not from_match:
        return results

    module_name = from_match.group(1)
    for part in from_match.group(2).split(","):
        token = part.strip()
        if not token:
            continue
        name, alias = _split_alias(token, separator=" as ")
        import_name = module_name if name == "*" else f"{module_name}.{name}"
        alias_name = alias or name
        aliases = {} if alias_name == "*" else {alias_name: import_name}
        results.append((import_name, aliases))
    return results


def _parse_js_imports(text: str) -> list[tuple[str, dict[str, str]]]:
    compact = " ".join(text.split())
    source_match = re.search(r"""from\s+["']([^"']+)["']""", compact)
    if source_match is None:
        source_match = re.search(r"""import\s+["']([^"']+)["']""", compact)
    if source_match is None:
        return []

    import_source = source_match.group(1)
    aliases: dict[str, str] = {}

    default_match = re.match(r"import\s+([A-Za-z_$][\w$]*)", compact)
    if default_match:
        aliases[default_match.group(1)] = import_source

    namespace_match = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", compact)
    if namespace_match:
        aliases[namespace_match.group(1)] = import_source

    named_match = re.search(r"\{([^}]+)\}", compact)
    if named_match:
        for item in named_match.group(1).split(","):
            token = item.strip()
            if not token:
                continue
            name, alias = _split_alias(token, separator=" as ")
            aliases[alias or name] = f"{import_source}.{name}"

    return [(import_source, aliases)]


def _parse_block_imports(text: str, language: str) -> list[tuple[str, dict[str, str]]]:
    if language != "go":
        return []
    modules = re.findall(r'"([^"]+)"', text)
    return [(module, {module.rsplit("/", 1)[-1]: module}) for module in modules]


def _parse_single_import(text: str, language: str) -> list[tuple[str, dict[str, str]]]:
    if language != "java":
        return []
    match = re.match(r"import\s+(?:static\s+)?([\w.]+)\s*;", " ".join(text.split()))
    if match is None:
        return []
    import_name = match.group(1)
    return [(import_name, {import_name.split(".")[-1]: import_name})]


def _parse_rust_imports(text: str) -> list[tuple[str, dict[str, str]]]:
    compact = " ".join(text.split()).removeprefix("use ").rstrip(";")
    if not compact:
        return []
    aliases: dict[str, str] = {}
    name, alias = _split_alias(compact, separator=" as ")
    aliases[(alias or name).split("::")[-1]] = name
    return [(name, aliases)]


def _parse_c_family_include(text: str) -> list[tuple[str, dict[str, str]]]:
    match = re.search(r'[<"]([^>"]+)[>"]', text)
    if match is None:
        return []
    include_name = match.group(1)
    alias = Path(include_name).stem
    return [(include_name, {alias: include_name})]


def _parse_csharp_using(text: str) -> list[tuple[str, dict[str, str]]]:
    match = re.match(r"using\s+([\w.]+)\s*;", " ".join(text.split()))
    if match is None:
        return []
    namespace = match.group(1)
    return [(namespace, {namespace.split(".")[-1]: namespace})]


def _parse_ruby_require(text: str, module_name: str) -> list[tuple[str, dict[str, str]]]:
    match = re.match(r"""(?:require|require_relative)\s+["']([^"']+)["']""", " ".join(text.split()))
    if match is None:
        return []
    dependency = match.group(1)
    alias = Path(dependency).stem or module_name
    return [(dependency, {alias: dependency})]


def _extract_inheritance(node: Any, source: bytes) -> list[str]:
    text = " ".join(_text(node, source).split())
    patterns = (
        r"class\s+\w+\(([^)]*)\)",
        r"extends\s+([\w.:, ]+)",
        r"class\s+\w+\s*:\s*([^{]+)",
        r"implements\s+([\w., ]+)",
    )
    results: list[str] = []
    for pattern in patterns:
        match = re.search(pattern, text)
        if match is None:
            continue
        for part in match.group(1).split(","):
            candidate = _normalize_reference(part)
            if candidate:
                results.append(candidate)
    return results


def _extract_call_target(node: Any, source: bytes, profile: LanguageProfile) -> str | None:
    if node.type == "method_invocation":
        object_node = node.child_by_field_name("object")
        name_node = node.child_by_field_name("name")
        if object_node is not None and name_node is not None:
            return _normalize_reference(f"{_text(object_node, source)}.{_text(name_node, source)}")
        if name_node is not None:
            return _normalize_reference(_text(name_node, source))

    for field_name in profile.call_target_fields:
        child = node.child_by_field_name(field_name)
        if child is None:
            continue
        target = _normalize_reference(_text(child, source))
        if target:
            return target

    for child in node.named_children:
        if child.type in {"arguments", "argument_list", "type_arguments", "block"}:
            continue
        target = _normalize_reference(_text(child, source))
        if target:
            return target
    return None


def _resolve_call(raw_target: str, state: ParseState, callable_owner: CallableOwner) -> str:
    if raw_target.startswith(("self.", "cls.")) and callable_owner.class_symbol:
        suffix = raw_target.split(".", 1)[1]
        method_symbol = state.class_methods.get(callable_owner.class_symbol, {}).get(suffix)
        if method_symbol:
            return method_symbol

    resolved, is_known = _try_resolve_reference(raw_target, state, callable_owner.class_symbol)
    if is_known:
        return resolved

    if "." not in raw_target and callable_owner.class_symbol:
        method_symbol = state.class_methods.get(callable_owner.class_symbol, {}).get(raw_target)
        if method_symbol:
            return method_symbol

    return f"unresolved::{raw_target}"


def _resolve_reference(raw_reference: str, state: ParseState, class_symbol: str | None) -> str:
    return _try_resolve_reference(raw_reference, state, class_symbol)[0]


def _try_resolve_reference(
    raw_reference: str, state: ParseState, class_symbol: str | None
) -> tuple[str, bool]:
    normalized = _normalize_reference(raw_reference)
    if not normalized:
        return raw_reference, False

    if normalized in state.import_aliases:
        return state.import_aliases[normalized], True

    if "." in normalized:
        head, tail = normalized.split(".", 1)
        if head in state.import_aliases:
            return f"{state.import_aliases[head]}.{tail}", True
        for symbol in state.symbols_by_short_name.get(head, []):
            return f"{symbol}.{tail}", True
        if head in {"self", "cls"} and class_symbol:
            return f"{class_symbol}.{tail}", True
        return normalized, False

    if class_symbol:
        method_symbol = state.class_methods.get(class_symbol, {}).get(normalized)
        if method_symbol:
            return method_symbol, True

    if normalized in state.symbols_by_short_name:
        return state.symbols_by_short_name[normalized][0], True

    return normalized, False


def _split_alias(token: str, separator: str) -> tuple[str, str | None]:
    if separator in token:
        left, right = token.split(separator, 1)
        return left.strip(), right.strip()
    return token.strip(), None


def _module_name(path: Path, repo_root: Path) -> str:
    try:
        relative = path.relative_to(repo_root)
    except ValueError:
        relative = Path(path.name)

    parts = list(relative.parts)
    if not parts:
        return path.stem

    parts[-1] = Path(parts[-1]).stem
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(part for part in parts if part) or path.stem


def _relative_file(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.name


def _normalize_reference(text: str) -> str:
    normalized = re.sub(r"\s+", "", text)
    normalized = normalized.replace("->", ".").replace("::", ".")
    normalized = normalized.replace("?.", ".")
    normalized = normalized.strip("&*")
    normalized = normalized.strip("()")
    return normalized


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")
