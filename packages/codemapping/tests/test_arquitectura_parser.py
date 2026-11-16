"""KC-CM-11-3 — Prueba de arquitectura: añadir un lenguaje solo requiere gramática y mapa de tipos de nodo.

El analizador no puede ramificar por lenguaje: todo lo propio de cada uno vive como datos en
`parser_treesitter.LENGUAJES`. Aquí se comprueba de tres formas:
  1. Estática: ninguna función de parser_treesitter compara nada contra el nombre de un lenguaje.
  2. Dinámica: un lenguaje nuevo registrado solo con gramática + mapa (sin tocar el código) produce símbolos.
  3. Consistencia: toda extensión de ingest_repo tiene su entrada y su gramática carga.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest
from kallicode_codemapping import parser_treesitter
from kallicode_codemapping.ingest_repo import LANG_BY_EXT, SourceFile
from kallicode_codemapping.parser_treesitter import LENGUAJES, Lenguaje, parse_file
from tree_sitter_languages import get_parser

# Lenguajes que se ingieren sin analizador de símbolos (los lee detectores_orm).
SIN_ANALIZADOR = {"prisma"}
REQUERIDOS = {
    "typescript",
    "tsx",
    "javascript",
    "python",
    "java",
    "c_sharp",
    "go",
    "php",
    "kotlin",
    "sql",
}


def test_el_codigo_del_analizador_no_ramifica_por_lenguaje() -> None:
    """Ninguna comparación ni pertenencia dentro de una función usa un nombre de lenguaje como literal."""
    nombres = set(LENGUAJES) | {"c#", "csharp", "golang", "kt"}
    arbol = ast.parse(inspect.getsource(parser_treesitter))
    infractores: list[str] = []
    for funcion in (n for n in ast.walk(arbol) if isinstance(n, ast.FunctionDef)):
        for nodo in ast.walk(funcion):
            if not isinstance(nodo, ast.Compare):
                continue
            literales = {
                c.value
                for c in ast.walk(nodo)
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
            }
            if literales & nombres:
                infractores.append(f"{funcion.name}:{nodo.lineno}: {sorted(literales & nombres)}")
    assert not infractores, f"El parser ramifica por lenguaje (usa Lenguaje): {infractores}"


def test_los_lenguajes_requeridos_estan_registrados() -> None:
    assert REQUERIDOS <= set(LENGUAJES)


def test_toda_extension_tiene_lenguaje_y_gramatica() -> None:
    for ext, lenguaje in LANG_BY_EXT.items():
        if lenguaje in SIN_ANALIZADOR:
            assert lenguaje not in LENGUAJES, f"{ext}: {lenguaje} no debería tener analizador"
            continue
        assert lenguaje in LENGUAJES, f"{ext} -> {lenguaje} no está en LENGUAJES"


@pytest.mark.parametrize("lenguaje", sorted(REQUERIDOS))
def test_la_gramatica_carga_y_el_mapa_no_esta_vacio(lenguaje: str) -> None:
    spec = LENGUAJES[lenguaje]
    assert spec.captura, lenguaje
    assert get_parser(spec.gramatica or lenguaje) is not None
    assert set(spec.captura.values()) <= {
        "function",
        "method",
        "class",
        "interface",
        "enum",
        "type",
        "view",
        "procedure",
    }


def test_un_lenguaje_nuevo_solo_necesita_gramatica_y_mapa(monkeypatch: pytest.MonkeyPatch) -> None:
    """«Golang» registrado con la gramática de Go y su mapa de nodos, sin una línea más de código."""
    monkeypatch.setitem(
        LENGUAJES,
        "golang_prueba",
        Lenguaje(
            gramatica="go",
            captura={"function_declaration": "function", "method_declaration": "method"},
            accesos={"selector_expression": ("field",)},
            invocado_ultimo_identificador=True,
        ),
    )
    codigo = "package x\nfunc (s *S) Run() { s.repo.Guardar(1); ayuda() }\nfunc ayuda() {}\n"
    simbolos = parse_file(
        SourceFile(path="x.golang", language="golang_prueba", content=codigo, sha256="h")
    )
    assert {(s.kind, s.name) for s in simbolos} == {("method", "Run"), ("function", "ayuda")}
    run = next(s for s in simbolos if s.name == "Run")
    assert run.language == "golang_prueba"
    assert run.signature == "func (s *S) Run()"
    assert set(run.calls) == {"Guardar", "ayuda"}


def test_lenguaje_sin_registro_devuelve_vacio() -> None:
    sf = SourceFile(path="a.zig", language="zig", content="pub fn main() void {}", sha256="h")
    assert parse_file(sf) == []


def test_capture_es_derivado_de_lenguajes() -> None:
    """CAPTURE se conserva por compatibilidad, pero la fuente de verdad es LENGUAJES."""
    assert set(parser_treesitter.CAPTURE) == set(LENGUAJES)
    for k, spec in LENGUAJES.items():
        assert parser_treesitter.CAPTURE[k] == dict(spec.captura)


def test_la_fuente_no_depende_de_un_archivo_de_prueba() -> None:
    assert Path(parser_treesitter.__file__).name == "parser_treesitter.py"
