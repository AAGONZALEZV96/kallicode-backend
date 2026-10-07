"""Fase 2 - extracción sintáctica con tree-sitter (multi-lenguaje).

Incluye optimizaciones de AST para KC-CM-04-5:
- Límite de profundidad de recursión para evitar RecursionError.
- Aislamiento de llamadas en funciones y closures anidados.
- Extracción limpia de firmas sin arrastrar el cuerpo de la función.
- Optimización en el manejo de imports a nivel de archivo.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from tree_sitter_languages import get_parser

MAX_SNIPPET = 1200
MAX_AST_DEPTH = 50

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
        return str(child.text.decode("utf-8", errors="replace"))

    parent = node.parent
    if parent is not None:
        if parent.type == "variable_declarator":
            key = parent.child_by_field_name("name")
            if key is not None:
                return str(key.text.decode("utf-8", errors="replace"))
        if parent.type == "pair":
            key = parent.child_by_field_name("key")
            if key is not None:
                return str(key.text.decode("utf-8", errors="replace").strip('"').strip("'"))

    return "<anonymous>"


def _slice(src: bytes, start: int, end: int) -> str:
    """tree-sitter entrega offsets en BYTES: se corta el buffer UTF-8, nunca el str."""
    return src[start:end].decode("utf-8", errors="replace")


def _extraer_firma_limpia(node: Any, src: bytes, language: str) -> str:
    """Extrae la cabecera limpia de la función o método según el lenguaje."""
    node_text = _slice(src, node.start_byte, node.end_byte)
    lineas = node_text.splitlines()
    if not lineas:
        return ""

    if language == "python":
        # En Python, el encabezado termina antes de los dos puntos del bloque o en el body
        body = node.child_by_field_name("body")
        if body is not None:
            raw_sig = _slice(src, node.start_byte, body.start_byte).strip()
            # Remover los dos puntos finales ':' si existen
            if raw_sig.endswith(":"):
                raw_sig = raw_sig[:-1].strip()
            return " ".join(raw_sig.split())
        primera = lineas[0].strip()
        if ":" in primera:
            return primera.split(":")[0].strip()
        return primera

    # En TypeScript, JavaScript y Java cortamos antes del bloque {
    body = node.child_by_field_name("body")
    if body is not None:
        raw_sig = _slice(src, node.start_byte, body.start_byte).strip()
        if raw_sig:
            return " ".join(raw_sig.split())

    # Fallback para interfaces o type aliases: primera línea limpia
    primera_linea = lineas[0].strip()
    if "{" in primera_linea:
        return primera_linea.split("{")[0].strip()
    return primera_linea


def _extraer_nombre_invocado(fn_node: Any) -> str:
    """Extrae el identificador final invocado (soporta llamadas simples y métodos)."""
    if not fn_node:
        return ""
    # Si es llamada a propiedad/método tipo obj.metodo(), extraer el atributo final
    if fn_node.type in ("member_expression", "attribute"):
        prop = fn_node.child_by_field_name("property") or fn_node.child_by_field_name("attribute")
        if prop and prop.text:
            return prop.text.decode("utf-8", errors="ignore").strip()
    if fn_node.text:
        return fn_node.text.decode("utf-8", errors="ignore").strip()
    return ""


def _collect_calls(node: Any, depth: int = 0) -> list[str]:
    """Recolecta identificadores de funciones llamadas con cota de profundidad."""
    if depth > MAX_AST_DEPTH:
        return []

    calls: list[str] = []
    nested_function_types = {
        "function_definition",
        "arrow_function",
        "function_declaration",
        "method_definition",
        "generator_function_declaration",
    }

    for child in node.children:
        # Aislamiento: si el hijo es una función anidada o closure, no atribuir sus llamadas al padre
        if child.type in nested_function_types and depth > 0:
            continue

        if child.type in ("call_expression", "call"):
            fn_node = child.child_by_field_name("function") or (
                child.children[0] if child.children else None
            )
            nombre_invocado = _extraer_nombre_invocado(fn_node)
            if nombre_invocado:
                calls.append(nombre_invocado)

        calls.extend(_collect_calls(child, depth=depth + 1))

    return calls


def _file_imports(root: Any, src: bytes) -> list[str]:
    return [
        _slice(src, c.start_byte, c.end_byte)[:200] for c in root.children if "import" in c.type
    ]


def parse_file(sf: Any) -> list[Symbol]:
    kinds = CAPTURE.get(sf.language)
    if not kinds:
        return []

    parser = get_parser(sf.language)
    src: bytes = sf.content.encode("utf-8") if isinstance(sf.content, str) else sf.content
    tree = parser.parse(src)
    imports = _file_imports(tree.root_node, src)

    symbols: list[Symbol] = []
    stack = [tree.root_node]

    while stack:
        node = stack.pop()
        kind = kinds.get(node.type)
        if kind and node.is_named:
            # Recolectar llamadas usando la función con límite de profundidad
            calls = _collect_calls(node, depth=0)
            text = _slice(src, node.start_byte, node.end_byte)
            name = _name_of(node)
            signature = _extraer_firma_limpia(node, src, sf.language)[:300]

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
                    signature=signature,
                    snippet=text[:MAX_SNIPPET],
                    calls=sorted(set(calls))[:50],
                    imports=imports,
                )
            )
        stack.extend(node.children)

    return symbols
