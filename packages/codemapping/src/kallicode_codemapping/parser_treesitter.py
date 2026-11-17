"""Fase 2 - extracción sintáctica con tree-sitter (multi-lenguaje).

Incluye optimizaciones de AST para KC-CM-04-5:
- Límite de profundidad de recursión para evitar RecursionError.
- Aislamiento de llamadas en funciones y closures anidados.
- Extracción limpia de firmas sin arrastrar el cuerpo de la función.
- Optimización en el manejo de imports a nivel de archivo.

KC-CM-11-3: cada lenguaje es un registro de datos (`Lenguaje`) en `LENGUAJES`: su gramática y su mapa de
tipos de nodo. Añadir un lenguaje NO toca la lógica de `parse_file` (lo verifica
`test_arquitectura_parser.py`): se agrega una entrada y la extensión en `ingest_repo.LANG_BY_EXT`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from tree_sitter_languages import get_parser

MAX_SNIPPET = 1200
MAX_AST_DEPTH = 50

# Funciones y closures anidados: sus llamadas no se atribuyen al símbolo que los contiene.
FUNCIONES_ANIDADAS_BASE: frozenset[str] = frozenset(
    {
        "function_definition",
        "arrow_function",
        "function_declaration",
        "method_definition",
        "generator_function_declaration",
    }
)
LLAMADAS_BASE: Mapping[str, str | None] = {"call_expression": "function", "call": "function"}
ACCESOS_BASE: Mapping[str, tuple[str, ...]] = {
    "member_expression": ("property",),
    "attribute": ("attribute",),
}
_ULTIMO_IDENTIFICADOR = re.compile(r"[^\W\d]\w*\s*$")


@dataclass(frozen=True)
class Lenguaje:
    """Todo lo que el analizador necesita saber de un lenguaje: datos, no código."""

    # Tipo de nodo tree-sitter -> kind del símbolo (function, method, class, interface, enum, type,
    # view, procedure).
    captura: Mapping[str, str]
    # Nombre de la gramática en tree-sitter-languages si difiere de la clave del lenguaje.
    gramatica: str | None = None
    # La firma de Python termina en ':'; vacío = sin terminador.
    terminador_firma: str = ""
    # Tipos de nodo de llamada -> campo con el invocado (None: el primer hijo del nodo).
    llamadas: Mapping[str, str | None] = field(default_factory=lambda: dict(LLAMADAS_BASE))
    # Funciones y closures anidados (se suman a FUNCIONES_ANIDADAS_BASE).
    anidadas: frozenset[str] = frozenset()
    # Nodos de acceso a miembro (obj.metodo) -> campos que contienen el nombre final.
    accesos: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: dict(ACCESOS_BASE))
    # Si el invocado trae calificadores (a.b, A::b, A\b, a->b), se queda con el último identificador.
    invocado_ultimo_identificador: bool = False
    # Imports de primer nivel además de los nodos cuyo tipo contiene "import".
    imports: frozenset[str] = frozenset()
    # Nodos sin campo `name`: tipos de hijo donde buscar el nombre (el último segmento si es calificado).
    hijos_nombre: tuple[str, ...] = ()
    # Nodos sin campo `body`: tipos de hijo que abren el cuerpo; la firma termina donde empiezan.
    hijos_cuerpo: tuple[str, ...] = ()
    # Sufijos que se quitan de la firma (el cuerpo puede empezar en "{" o en "=").
    sufijos_firma: tuple[str, ...] = ("{",)
    # Un `function` cuyo padre es uno de estos tipos es un `method` (clases de Kotlin).
    padres_metodo: frozenset[str] = frozenset()
    # Nodo -> (campo, {tipo del hijo en ese campo: kind}). Ej.: type_spec de Go según sea struct o interface.
    kind_por_campo: Mapping[str, tuple[str, Mapping[str, str]]] = field(default_factory=dict)
    # Nodo -> {texto de un token anónimo (en minúsculas): kind}. Ej.: PROCEDURE, interface, enum.
    kind_por_token: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


LENGUAJES: dict[str, Lenguaje] = {
    "typescript": Lenguaje(
        captura={
            "function_declaration": "function",
            "method_definition": "method",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
            "type_alias_declaration": "type",
            "arrow_function": "function",
            "function": "function",
        }
    ),
    "javascript": Lenguaje(
        captura={
            "function_declaration": "function",
            "method_definition": "method",
            "class_declaration": "class",
            "arrow_function": "function",
            "function": "function",
        }
    ),
    "python": Lenguaje(
        captura={"function_definition": "function", "class_definition": "class"},
        terminador_firma=":",
    ),
    "java": Lenguaje(
        captura={
            "method_declaration": "method",
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
        }
    ),
    # --- KC-CM-11-3 ---------------------------------------------------------------------------
    "c_sharp": Lenguaje(
        captura={
            "method_declaration": "method",
            "constructor_declaration": "method",
            "local_function_statement": "function",
            "class_declaration": "class",
            "struct_declaration": "class",
            "record_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
        },
        llamadas={"invocation_expression": "function"},
        anidadas=frozenset(
            {
                "lambda_expression",
                "anonymous_method_expression",
                "method_declaration",
                "constructor_declaration",
                "local_function_statement",
            }
        ),
        accesos={"member_access_expression": ("name",)},
        invocado_ultimo_identificador=True,
        imports=frozenset({"using_directive"}),
    ),
    "go": Lenguaje(
        captura={
            "function_declaration": "function",
            "method_declaration": "method",
            "method_spec": "method",
            "type_spec": "type",
        },
        anidadas=frozenset({"func_literal", "method_declaration"}),
        accesos={"selector_expression": ("field",)},
        invocado_ultimo_identificador=True,
        kind_por_campo={
            "type_spec": ("type", {"struct_type": "class", "interface_type": "interface"})
        },
    ),
    "php": Lenguaje(
        captura={
            "function_definition": "function",
            "method_declaration": "method",
            "class_declaration": "class",
            "trait_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
        },
        llamadas={
            "function_call_expression": "function",
            "member_call_expression": "name",
            "nullsafe_member_call_expression": "name",
            "scoped_call_expression": "name",
        },
        anidadas=frozenset(
            {
                "anonymous_function_creation_expression",
                "arrow_function",
                "method_declaration",
                "function_definition",
            }
        ),
        invocado_ultimo_identificador=True,
        imports=frozenset({"namespace_use_declaration"}),
    ),
    "kotlin": Lenguaje(
        captura={
            "function_declaration": "function",
            "class_declaration": "class",
            "object_declaration": "class",
        },
        llamadas={"call_expression": None},
        anidadas=frozenset({"anonymous_function", "lambda_literal"}),
        invocado_ultimo_identificador=True,
        sufijos_firma=("{", "="),
        padres_metodo=frozenset({"class_body"}),
        kind_por_token={"class_declaration": {"interface": "interface", "enum": "enum"}},
    ),
    # SQL: vistas, procedimientos y funciones. El cuerpo de una función es un literal de cadena
    # ($$ ... $$), por eso solo se ven las llamadas de las vistas; las tablas las cubre extract_sql.
    "sql": Lenguaje(
        captura={
            "create_view_statement": "view",
            "create_materialized_view_statement": "view",
            "create_function_statement": "function",
            "create_procedure_statement": "procedure",
        },
        llamadas={"function_call": None},
        invocado_ultimo_identificador=True,
        hijos_nombre=("dotted_name", "object_reference", "identifier"),
        hijos_cuerpo=("view_body", "function_body"),
        sufijos_firma=(),
        kind_por_token={"create_function_statement": {"procedure": "procedure"}},
    ),
}
LENGUAJES["tsx"] = LENGUAJES["typescript"]

# Compatibilidad: mapa lenguaje -> {tipo de nodo: kind}.
CAPTURE: dict[str, dict[str, str]] = {k: dict(v.captura) for k, v in LENGUAJES.items()}


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


def _texto(node: Any) -> str:
    return str(node.text.decode("utf-8", errors="replace"))


def _name_of(node: Any, spec: Lenguaje) -> str:
    child = node.child_by_field_name("name")
    if child is not None:
        return _texto(child)

    for hijo in node.children:
        if hijo.type in spec.hijos_nombre:
            return _texto(hijo).rsplit(".", 1)[-1].strip().strip('"`[]')

    parent = node.parent
    if parent is not None:
        if parent.type == "variable_declarator":
            key = parent.child_by_field_name("name")
            if key is not None:
                return _texto(key)
        if parent.type == "pair":
            key = parent.child_by_field_name("key")
            if key is not None:
                return _texto(key).strip('"').strip("'")

    return "<anonymous>"


def _slice(src: bytes, start: int, end: int) -> str:
    """tree-sitter entrega offsets en BYTES: se corta el buffer UTF-8, nunca el str."""
    return src[start:end].decode("utf-8", errors="replace")


def _extraer_firma_limpia(node: Any, src: bytes, spec: Lenguaje) -> str:
    """Extrae la firma del símbolo respetando los contratos de golden AST."""
    body = node.child_by_field_name("body")
    if body is None and spec.hijos_cuerpo:
        body = next((h for h in node.children if h.type in spec.hijos_cuerpo), None)
    if body is not None:
        raw_sig = _slice(src, node.start_byte, body.start_byte).strip()
        if spec.terminador_firma:
            # El contrato de Python exige terminar estrictamente en ':'
            if not raw_sig.endswith(spec.terminador_firma):
                raw_sig += spec.terminador_firma
            return " ".join(raw_sig.splitlines()).strip()
        # Para Java, TypeScript y JavaScript:
        # Se remueven llaves residuales al final respetando el prefijo del snippet
        for sufijo in spec.sufijos_firma:
            raw_sig = raw_sig.removesuffix(sufijo).rstrip()
        return " ".join(raw_sig.split()) if spec.hijos_cuerpo else raw_sig.rstrip()

    # Si no tiene nodo 'body' identificado (interfaces, types o funciones compactas)
    node_text = _slice(src, node.start_byte, node.end_byte)
    lineas = node_text.splitlines()
    if not lineas:
        return ""

    if spec.terminador_firma:
        primera = lineas[0].strip()
        if not primera.endswith(spec.terminador_firma):
            primera = primera.split(spec.terminador_firma)[0].strip() + spec.terminador_firma
        return primera

    # Java / TS / JS fallback
    primera_linea = lineas[0].strip()
    if "{" in primera_linea:
        return primera_linea.split("{")[0].rstrip()
    return primera_linea


def _normalizar_invocado(texto: str, spec: Lenguaje) -> str:
    if not spec.invocado_ultimo_identificador:
        return texto
    # a.b.c / A::c / A\c / a->c / c<T>: solo el último identificador.
    sin_genericos = re.sub(r"<[^<>]*>\s*$", "", texto)
    m = _ULTIMO_IDENTIFICADOR.search(sin_genericos)
    return m.group(0).strip() if m else ""


def _extraer_nombre_invocado(fn_node: Any, spec: Lenguaje) -> str:
    """Extrae el identificador final invocado (soporta llamadas simples y métodos)."""
    if not fn_node:
        return ""
    # Si es llamada a propiedad/método tipo obj.metodo(), extraer el atributo final
    for campo in spec.accesos.get(fn_node.type, ()):
        prop = fn_node.child_by_field_name(campo)
        if prop and prop.text:
            return _normalizar_invocado(prop.text.decode("utf-8", errors="ignore").strip(), spec)
    if fn_node.text:
        return _normalizar_invocado(fn_node.text.decode("utf-8", errors="ignore").strip(), spec)
    return ""


def _collect_calls(node: Any, spec: Lenguaje, depth: int = 0) -> list[str]:
    """Recolecta identificadores de funciones llamadas con cota de profundidad."""
    if depth > MAX_AST_DEPTH:
        return []

    calls: list[str] = []
    nested_function_types = FUNCIONES_ANIDADAS_BASE | spec.anidadas

    for child in node.children:
        # Aislamiento: si el hijo es una función anidada o closure, no atribuir sus llamadas al padre
        if child.type in nested_function_types and depth > 0:
            continue

        if child.type in spec.llamadas:
            campo = spec.llamadas[child.type]
            fn_node = (child.child_by_field_name(campo) if campo else None) or (
                child.children[0] if child.children else None
            )
            nombre_invocado = _extraer_nombre_invocado(fn_node, spec)
            if nombre_invocado:
                calls.append(nombre_invocado)

        calls.extend(_collect_calls(child, spec, depth=depth + 1))

    return calls


def _file_imports(root: Any, src: bytes, spec: Lenguaje) -> list[str]:
    return [
        _slice(src, c.start_byte, c.end_byte)[:200]
        for c in root.children
        if "import" in c.type or c.type in spec.imports
    ]


def _kind_de(node: Any, base: str, spec: Lenguaje) -> str:
    """Afina el kind según el mapa del lenguaje: campo, token anónimo o padre."""
    por_campo = spec.kind_por_campo.get(node.type)
    if por_campo is not None:
        campo, tipos = por_campo
        hijo = node.child_by_field_name(campo)
        if hijo is not None and hijo.type in tipos:
            return tipos[hijo.type]
    por_token = spec.kind_por_token.get(node.type)
    if por_token:
        for hijo in node.children:
            if hijo.is_named:
                continue
            texto = hijo.type.lower()
            for clave, kind in por_token.items():
                if clave in texto:
                    return kind
    if base == "function" and node.parent is not None and node.parent.type in spec.padres_metodo:
        return "method"
    return base


def parse_file(sf: Any) -> list[Symbol]:
    spec = LENGUAJES.get(sf.language)
    if spec is None or not spec.captura:
        return []
    kinds = spec.captura

    parser = get_parser(spec.gramatica or sf.language)
    src: bytes = sf.content.encode("utf-8") if isinstance(sf.content, str) else sf.content
    tree = parser.parse(src)
    imports = _file_imports(tree.root_node, src, spec)

    symbols: list[Symbol] = []
    stack = [tree.root_node]

    while stack:
        node = stack.pop()
        kind = kinds.get(node.type)
        if kind and node.is_named:
            kind = _kind_de(node, kind, spec)
            # Recolectar llamadas usando la función con límite de profundidad
            calls = _collect_calls(node, spec, depth=0)
            text = _slice(src, node.start_byte, node.end_byte)
            name = _name_of(node, spec)
            signature = _extraer_firma_limpia(node, src, spec)[:300]

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
