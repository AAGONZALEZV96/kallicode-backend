"""Fase 2 - extraccion sintactica con tree-sitter (multi-lenguaje).

La capa de CODIGO se construye de forma determinista: el LLM no
interviene aqui. Cada simbolo lleva firma, posicion y relaciones
sintacticas exactas (llamadas, imports). Anadir un lenguaje nuevo
es anadir su gramatica tree-sitter y su mapa de tipos de nodo.
"""
from dataclasses import dataclass, field

from tree_sitter_languages import get_parser

MAX_SNIPPET = 1200  # caracteres de cuerpo que alimentan el embedding

# Tipos de nodo AST que se capturan como simbolos, por lenguaje
CAPTURE = {
    "typescript": {
        "function_declaration": "function", "method_definition": "method",
        "class_declaration": "class", "interface_declaration": "interface",
        "enum_declaration": "enum", "type_alias_declaration": "type",
    },
    "javascript": {
        "function_declaration": "function", "method_definition": "method",
        "class_declaration": "class",
    },
    "python": {
        "function_definition": "function", "class_definition": "class",
    },
    "java": {
        "method_declaration": "method", "class_declaration": "class",
        "interface_declaration": "interface", "enum_declaration": "enum",
    },
}
CAPTURE["tsx"] = CAPTURE["typescript"]


@dataclass
class Symbol:
    id: str          # sha corto de (file, name, start_line)
    file: str
    language: str
    kind: str
    name: str
    start_line: int
    end_line: int
    signature: str
    snippet: str
    calls: list = field(default_factory=list)
    imports: list = field(default_factory=list)


def _name_of(node) -> str:
    child = node.child_by_field_name("name")
    return child.text.decode() if child is not None else "<anonymous>"


def _collect_calls(node, acc: list):
    """Llamadas dentro del cuerpo de un simbolo (call_expression / call)."""
    if node.type in ("call_expression", "call", "method_invocation"):
        fn = node.child_by_field_name("function") or \
             node.child_by_field_name("name")
        if fn is not None:
            acc.append(fn.text.decode().split("(")[0][-120:])
    for child in node.children:
        _collect_calls(child, acc)


def _file_imports(root, source: str) -> list:
    return [source[c.start_byte:c.end_byte][:200]
            for c in root.children if "import" in c.type]


def parse_file(sf) -> list[Symbol]:
    """Extrae los simbolos de un SourceFile (ver ingest_repo.SourceFile)."""
    import hashlib
    kinds = CAPTURE.get(sf.language)
    if not kinds:
        return []
    parser = get_parser(sf.language)
    tree = parser.parse(sf.content.encode())
    imports = _file_imports(tree.root_node, sf.content)
    symbols, stack = [], [tree.root_node]
    while stack:
        node = stack.pop()
        kind = kinds.get(node.type)
        if kind:
            calls: list = []
            _collect_calls(node, calls)
            text = sf.content[node.start_byte:node.end_byte]
            name = _name_of(node)
            sid = hashlib.sha1(
                f"{sf.path}:{name}:{node.start_point[0]}".encode()
            ).hexdigest()[:16]
            symbols.append(Symbol(
                id=sid, file=sf.path, language=sf.language, kind=kind,
                name=name,
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                signature=text.split("{")[0].strip()[:300],
                snippet=text[:MAX_SNIPPET],
                calls=sorted(set(calls))[:50],
                imports=imports,
            ))
        stack.extend(node.children)
    return symbols
