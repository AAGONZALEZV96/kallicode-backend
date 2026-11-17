"""KC-CM-11-3 — Golden de símbolos, firmas y llamadas por lenguaje: C#, Go, PHP, Kotlin y SQL.

Un fixture por lenguaje en tests/fixtures/lenguajes/. El golden fija la lista completa de símbolos (incluidos los
IDs); además cada lenguaje tiene un contrato mínimo legible (kinds, firmas y llamadas clave) para que un cambio
del mapa de nodos falle con un mensaje claro y no con un diff de JSON.

Regenerar los golden (solo cuando el cambio de salida es intencional):
    bash:        KC_UPDATE_GOLDEN=1 uv run pytest packages/codemapping/tests/test_golden_lenguajes.py
    PowerShell:  $env:KC_UPDATE_GOLDEN="1"; uv run pytest packages/codemapping/tests/test_golden_lenguajes.py
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from kallicode_codemapping.ingest_repo import LANG_BY_EXT, SourceFile
from kallicode_codemapping.parser_treesitter import parse_file

TESTS_DIR = Path(__file__).parent
FIXTURES = TESTS_DIR / "fixtures" / "lenguajes"
GOLDEN_DIR = TESTS_DIR / "golden" / "lenguajes"
UPDATE_GOLDEN = os.environ.get("KC_UPDATE_GOLDEN") == "1"

ARCHIVOS: dict[str, str] = {
    "Facturacion.cs": "c_sharp",
    "facturacion.go": "go",
    "Facturacion.php": "php",
    "Facturacion.kt": "kotlin",
    "vistas.sql": "sql",
}


def _simbolos(nombre: str) -> list[Any]:
    # Se lee como bytes y se decodifica: el contenido no depende de los saltos de línea del checkout.
    contenido = (FIXTURES / nombre).read_bytes().decode("utf-8").replace("\r\n", "\n")
    sf = SourceFile(
        path=nombre, language=LANG_BY_EXT[Path(nombre).suffix], content=contenido, sha256="x"
    )
    return parse_file(sf)


def _como_dicts(nombre: str) -> list[dict[str, Any]]:
    return sorted(
        (asdict(s) for s in _simbolos(nombre)),
        key=lambda s: (s["start_line"], s["end_line"], s["kind"], s["name"], s["id"]),
    )


def test_inventario_del_fixture() -> None:
    assert {p.name for p in FIXTURES.iterdir()} == set(ARCHIVOS)


@pytest.mark.parametrize("nombre,lenguaje", sorted(ARCHIVOS.items()))
def test_la_extension_resuelve_al_lenguaje(nombre: str, lenguaje: str) -> None:
    assert LANG_BY_EXT[Path(nombre).suffix] == lenguaje


@pytest.mark.parametrize("nombre", sorted(ARCHIVOS))
def test_golden(nombre: str) -> None:
    actual = _como_dicts(nombre)
    ruta = GOLDEN_DIR / f"{nombre}.json"
    if UPDATE_GOLDEN:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        texto = json.dumps(actual, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        ruta.write_text(texto, encoding="utf-8", newline="\n")
        return
    assert ruta.exists(), f"Falta el golden {ruta}. Genéralo con KC_UPDATE_GOLDEN=1."
    assert actual == json.loads(ruta.read_text(encoding="utf-8")), (
        f"La salida difiere de {ruta.relative_to(TESTS_DIR)}. Si el cambio es intencional, "
        "regenera con KC_UPDATE_GOLDEN=1 y revisa el diff."
    )


# --------------------------------------------------------------------------- contrato mínimo

SIMBOLOS_CLAVE: dict[str, set[tuple[str, str]]] = {
    "Facturacion.cs": {
        ("interface", "IRepositorio"),
        ("enum", "Estado"),
        ("class", "Monto"),  # struct
        ("class", "Cliente"),  # record
        ("class", "ServicioFacturas"),
        ("method", "Buscar"),
        ("method", "Iva"),  # cuerpo con =>
        ("function", "Local"),  # función local
    },
    "facturacion.go": {
        ("class", "Servicio"),  # struct
        ("interface", "Repositorio"),
        ("type", "Estado"),
        ("function", "Nuevo"),
        ("method", "Procesar"),  # con receptor
        ("method", "Buscar"),  # método de interfaz
    },
    "Facturacion.php": {
        ("interface", "Repositorio"),
        ("class", "Auditable"),  # trait
        ("enum", "Estado"),
        ("class", "Servicio"),
        ("method", "buscar"),
        ("function", "aplicar_iva"),
    },
    "Facturacion.kt": {
        ("interface", "Repositorio"),
        ("enum", "Estado"),
        ("class", "Servicio"),
        ("class", "Utilidades"),  # object
        ("method", "buscar"),  # función dentro de una clase
        ("function", "aplicarIva"),  # función de primer nivel
    },
    "vistas.sql": {
        ("view", "facturas_pagadas"),
        ("view", "resumen_mensual"),  # vista materializada
        ("function", "total_con_iva"),
        ("procedure", "cerrar_mes"),
    },
}


@pytest.mark.parametrize("nombre", sorted(SIMBOLOS_CLAVE))
def test_simbolos_clave(nombre: str) -> None:
    obtenidos = {(s.kind, s.name) for s in _simbolos(nombre)}
    assert SIMBOLOS_CLAVE[nombre] <= obtenidos, SIMBOLOS_CLAVE[nombre] - obtenidos


def _por_nombre(nombre: str, simbolo: str, kind: str) -> Any:
    return next(s for s in _simbolos(nombre) if s.name == simbolo and s.kind == kind)


@pytest.mark.parametrize(
    "archivo,simbolo,kind,firma",
    [
        ("Facturacion.cs", "Buscar", "method", "public Factura Buscar(int id)"),
        ("Facturacion.cs", "Iva", "method", "private static decimal Iva(decimal neto)"),
        ("facturacion.go", "Procesar", "method", "func (s *Servicio) Procesar(id int) error"),
        ("Facturacion.php", "aplicar_iva", "function", "function aplicar_iva(float $neto): float"),
        ("Facturacion.kt", "aplicarIva", "function", "fun aplicarIva(neto: Double): Double"),
        ("Facturacion.kt", "validar", "method", "private fun validar(f: Factura?)"),
        (
            "vistas.sql",
            "cerrar_mes",
            "procedure",
            "CREATE PROCEDURE reporte.cerrar_mes(IN p_mes date) LANGUAGE plpgsql",
        ),
        ("vistas.sql", "facturas_pagadas", "view", "CREATE VIEW reporte.facturas_pagadas"),
    ],
)
def test_firmas_sin_arrastrar_el_cuerpo(archivo: str, simbolo: str, kind: str, firma: str) -> None:
    assert _por_nombre(archivo, simbolo, kind).signature == firma


@pytest.mark.parametrize(
    "archivo,simbolo,kind,esperadas,ausentes",
    [
        # Miembros calificados y genéricos se reducen al último identificador.
        ("Facturacion.cs", "Buscar", "method", {"Buscar", "Total", "Validar"}, {"Total<decimal>"}),
        # Las llamadas de una lambda o de una función local no se atribuyen a quien las contiene.
        ("Facturacion.cs", "Procesar", "method", {"Registrar"}, {"LlamadaInterna", "Auxiliar"}),
        (
            "facturacion.go",
            "Procesar",
            "method",
            {"Buscar", "Guardar", "calcularTotal", "Errorf"},
            {"notificar"},
        ),
        ("Facturacion.php", "buscar", "method", {"buscar", "total", "validar"}, set()),
        ("Facturacion.php", "validar", "method", {"verificar"}, {"llamadaInterna"}),
        ("Facturacion.kt", "buscar", "method", {"buscar", "total", "validar", "abs"}, set()),
        ("Facturacion.kt", "procesar", "method", {"forEach"}, {"registrar"}),
        ("vistas.sql", "facturas_pagadas", "view", {"upper", "total_con_iva"}, set()),
    ],
)
def test_llamadas(
    archivo: str, simbolo: str, kind: str, esperadas: set[str], ausentes: set[str]
) -> None:
    llamadas = set(_por_nombre(archivo, simbolo, kind).calls)
    assert esperadas <= llamadas
    assert not (ausentes & llamadas)


def test_sql_ignora_tablas_y_no_rompe_con_sql_invalido() -> None:
    nombres = {s.name for s in _simbolos("vistas.sql")}
    assert "auditoria" not in nombres  # CREATE TABLE lo cubre extract_sql, no el analizador
    sf = SourceFile(path="roto.sql", language="sql", content="CREATE VIEW AS ((( ;;", sha256="x")
    assert isinstance(parse_file(sf), list)


@pytest.mark.parametrize("lenguaje", ["c_sharp", "go", "php", "kotlin", "sql"])
def test_ids_deterministas(lenguaje: str) -> None:
    nombre = next(n for n, lg in ARCHIVOS.items() if lg == lenguaje)
    assert [s.id for s in _simbolos(nombre)] == [s.id for s in _simbolos(nombre)]
    assert len({s.id for s in _simbolos(nombre)}) == len(_simbolos(nombre))
