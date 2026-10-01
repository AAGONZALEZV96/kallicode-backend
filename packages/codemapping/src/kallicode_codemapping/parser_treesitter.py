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


def _slice(src: bytes, start: int, end: int) -> str:
    """tree-sitter entrega offsets en BYTES: se corta el buffer UTF-8, nunca el str.

    Cortar el str con offsets en bytes desplaza el resultado apenas aparece un
    carácter no ASCII (ñ, á) antes del nodo: 'def enviar_sii' quedaba como '_sii'.
    """
    return src[start:end].decode("utf-8", errors="replace")


def _signature_of(node: Any, src: bytes) -> str:
    body = node.child_by_field_name("body")
    if body is not None:
        # Corta justo donde empieza el nodo 'body' real (no el primer '{' que aparezca).
        return _slice(src, node.start_byte, body.start_byte).strip()

    # Sin nodo 'body' identificable (ej. interface, type alias): usa la primera línea.
    return _slice(src, node.start_byte, node.end_byte).split("\n")[0].strip()


def _collect_calls(node: Any, acc: list[str]) -> None:
    if node.type in ("call_expression", "call", "method_invocation"):
        fn = node.child_by_field_name("function") or node.child_by_field_name("name")
        if fn is not None:
            acc.append(fn.text.decode().split("(")[0][-120:])
    for child in node.children:
        _collect_calls(child, acc)


def _file_imports(root: Any, src: bytes) -> list[str]:
    return [
        _slice(src, c.start_byte, c.end_byte)[:200] for c in root.children if "import" in c.type
    ]


def parse_file(sf: Any) -> list[Symbol]:
    kinds = CAPTURE.get(sf.language)
    if not kinds:
        return []
    parser = get_parser(sf.language)
    src = sf.content.encode("utf-8")
    tree = parser.parse(src)
    imports = _file_imports(tree.root_node, src)
    symbols: list[Symbol] = []
    stack = [tree.root_node]
    while stack:
        node = stack.pop()
        kind = kinds.get(node.type)
        if kind and node.is_named:
            calls: list[str] = []
            _collect_calls(node, calls)
            text = _slice(src, node.start_byte, node.end_byte)
            name = _name_of(node)
            sid = hashlib.sha256(
                f"{sf.path}:{name}:{node.start_point[0]}:{node.start_point[1]}".encode()
            ).hexdigest()[:16]
            symbols.append(
                Symbol(
                    id=sid,
                    file=sf.path,
                    language=sf.language,
                    kind=kind,
                    name=name,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=_signature_of(node, src)[:300],
                    snippet=text[:MAX_SNIPPET],
                    calls=sorted(set(calls))[:50],
                    imports=imports,
                )
            )
        stack.extend(node.children)
    return symbols
