"""Fase 2 - extracción sintáctica con tree-sitter (multi-lenguaje)."""

import hashlib
from dataclasses import dataclass, field
from typing import Any

from tree_sitter_languages import get_parser

MAX_SNIPPET = 1200

CAPTURE: dict[str, dict[str, str]] = {
    "typescript": {
        "function_declaration": "function",
        "method_definition": "method",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
        "type_alias_declaration": "type",
        "arrow_function": "function",
        "function": "function",
    },
    "javascript": {
        "function_declaration": "function",
        "method_definition": "method",
        "class_declaration": "class",
        "arrow_function": "function",
        "function": "function",
    },
    "python": {
        "function_definition": "function",
        "class_definition": "class",
    },
    "java": {
        "method_declaration": "method",
        "class_declaration": "class",
        "interface_declaration": "interface",
        "enum_declaration": "enum",
    },
}
CAPTURE["tsx"] = CAPTURE["typescript"]


@dataclass
class Symbol:
    id: str
    file: str
    language: str
    kind: str
    name: str
    start_line: int
    end_line: int
    signature: str
    snippet: str
    calls: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)


def _name_of(node: Any) -> str:
    child = node.child_by_field_name("name")
    if child is not None:
        return str(child.text.decode())

    parent = node.parent
    if parent is not None:
        if parent.type == "variable_declarator":
            key = parent.child_by_field_name("name")
            if key is not None:
                return str(key.text.decode())
        if parent.type == "pair":
            key = parent.child_by_field_name("key")
            if key is not None:
                return str(key.text.decode().strip('"').strip("'"))

    return "<anonymous>"


def _signature_of(node: Any, text: str) -> str:
    body = node.child_by_field_name("body")
    if body is not None:
        # Corta justo donde empieza el nodo 'body' real (no el primer '{' que aparezca).
        cut = body.start_byte - node.start_byte
        return text[:cut].strip()

    # Sin nodo 'body' identificable (ej. interface, type alias): usa la primera línea.
    return text.split("\n")[0].strip()


def _collect_calls(node: Any, acc: list[str]) -> None:
    if node.type in ("call_expression", "call", "method_invocation"):
        fn = node.child_by_field_name("function") or node.child_by_field_name("name")
        if fn is not None:
            acc.append(fn.text.decode().split("(")[0][-120:])
    for child in node.children:
        _collect_calls(child, acc)


def _file_imports(root: Any, source: str) -> list[str]:
    return [source[c.start_byte : c.end_byte][:200] for c in root.children if "import" in c.type]


def parse_file(sf: Any) -> list[Symbol]:
    kinds = CAPTURE.get(sf.language)
    if not kinds:
        return []
    parser = get_parser(sf.language)
    tree = parser.parse(sf.content.encode())
    imports = _file_imports(tree.root_node, sf.content)
    symbols: list[Symbol] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        kind = kinds.get(node.type)
        if kind and node.is_named:
            calls: list[str] = []
            _collect_calls(node, calls)
            text = sf.content[node.start_byte : node.end_byte]
            name = _name_of(node)
            sid = hashlib.sha256(f"{sf.path}:{name}:{node.start_point[0]}:{node.start_point[1]}".encode()).hexdigest()[
                :16
            ]
            symbols.append(
                Symbol(
                    id=sid,
                    file=sf.path,
                    language=sf.language,
                    kind=kind,
                    name=name,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=_signature_of(node, text)[:300],
                    snippet=text[:MAX_SNIPPET],
                    calls=sorted(set(calls))[:50],
                    imports=imports,
                )
            )
        stack.extend(node.children)
    return symbols
