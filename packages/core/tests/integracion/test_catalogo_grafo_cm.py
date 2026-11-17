"""Catálogo de nodos y aristas del grafo generado desde el esquema cm (KC-CM-12-3).

Criterio de aceptación: «el catálogo de nodos y aristas se genera desde el DDL del esquema cm para que no se
desactualice, y su generación corre en CI».

  * test_el_catalogo_versionado_coincide_con_el_esquema: docs/catalogo_grafo_cm.md es lo que sale del esquema.
  * test_el_esquema_cm_es_coherente: node_types, edge_types, los CHECK de cm.edges y los triggers de cm.nodes
    dicen lo mismo.
  * test_un_tipo_nuevo_*: un tipo añadido solo a una de las fuentes se detecta y aparece en el documento.

Si la primera falla tras una migración, regenera el documento:
    python db/tools/catalogo_grafo_cm.py
Las pruebas que modifican el esquema lo hacen dentro de una transacción que SIEMPRE se revierte.
"""

from __future__ import annotations

import difflib
import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

RAIZ = Path(__file__).resolve().parents[4]
HERRAMIENTA = RAIZ / "db" / "tools" / "catalogo_grafo_cm.py"


def _cargar_herramienta() -> ModuleType:
    spec = importlib.util.spec_from_file_location("catalogo_grafo_cm", HERRAMIENTA)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


cg = _cargar_herramienta()


def _conectar() -> psycopg.Connection[dict[str, Any]]:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    esquema, sep, resto = url.partition("://")
    dsn = f"{esquema.split('+', 1)[0]}://{resto}" if sep else url
    try:
        conn = psycopg.connect(dsn, autocommit=False, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    fila = conn.execute("SELECT to_regclass('cm.node_types') AS t").fetchone()
    if not fila or fila["t"] is None:
        conn.close()
        pytest.skip("esquema cm sin migrar (021)")
    return conn


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    c = _conectar()
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture(scope="module")
def modelo() -> dict[str, Any]:
    c = _conectar()
    try:
        datos: dict[str, Any] = cg.extraer(c)
        return datos
    finally:
        c.rollback()
        c.close()


# ---------------------------------------------------------------------------
# El documento versionado y la coherencia del esquema
# ---------------------------------------------------------------------------
def test_el_catalogo_versionado_coincide_con_el_esquema(modelo: dict[str, Any]) -> None:
    generado = cg.renderizar(modelo)
    versionado = cg.DOCUMENTO.read_text(encoding="utf-8")
    if generado != versionado:
        diff = difflib.unified_diff(
            versionado.splitlines(), generado.splitlines(), "versionado", "generado", n=0
        )
        pytest.fail(
            f"docs/catalogo_grafo_cm.md difiere del esquema cm. Regenera con: {cg.REGENERAR}\n"
            + "\n".join(list(diff)[:40])
        )


def test_el_esquema_cm_es_coherente(modelo: dict[str, Any]) -> None:
    assert cg.incoherencias(modelo) == []


def test_el_catalogo_cubre_todos_los_tipos(modelo: dict[str, Any]) -> None:
    texto = cg.renderizar(modelo)
    for n in modelo["tipos_nodo"]:
        assert f"| `{n['kind']}` |" in texto
    for a in modelo["tipos_arista"]:
        assert f"| `{a['tipo']}` |" in texto
    # Cada kind tiene su tabla de respaldo, deducida de los triggers de cm.nodes.
    assert set(modelo["respaldo"]) == {n["kind"] for n in modelo["tipos_nodo"]}
    assert modelo["respaldo"]["symbol"]["tablas"] == ["cm.symbols"]
    assert "cm.doc_chunk_links" in {r["tabla"] for r in modelo["relaciones_externas"]}


def test_el_catalogo_es_determinista(modelo: dict[str, Any]) -> None:
    assert cg.renderizar(modelo) == cg.renderizar(modelo)


# ---------------------------------------------------------------------------
# Un cambio en una sola fuente se detecta
# ---------------------------------------------------------------------------
def test_un_tipo_de_arista_nuevo_sin_check_se_detecta(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    conn.execute("SET LOCAL ROLE kallicode_admin")
    conn.execute(
        "INSERT INTO cm.edge_types (tipo, es_dirigida, descripcion) "
        "VALUES ('ZZ_PRUEBA', true, 'Tipo de prueba')"
    )
    modelo = cg.extraer(conn)
    errores = cg.incoherencias(modelo)
    assert any("edges_tipo_chk" in e and "ZZ_PRUEBA" in e for e in errores), errores
    assert "| `ZZ_PRUEBA` | sí | Tipo de prueba |" in cg.renderizar(modelo)


def test_un_tipo_de_nodo_sin_tabla_ni_check_se_detecta(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    conn.execute("SET LOCAL ROLE kallicode_admin")
    conn.execute(
        "INSERT INTO cm.node_types (kind, categoria, descripcion) "
        "VALUES ('zz_prueba', 'codigo', 'Nodo de prueba')"
    )
    errores = cg.incoherencias(cg.extraer(conn))
    assert any("src_kind" in e and "zz_prueba" in e for e in errores), errores
    assert any("dst_kind" in e and "zz_prueba" in e for e in errores), errores
    assert any("nodes_sync" in e and "zz_prueba" in e for e in errores), errores


def test_un_kind_del_check_fuera_del_catalogo_se_detecta(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    conn.execute("SET LOCAL ROLE kallicode_admin")
    conn.execute("ALTER TABLE cm.edges DROP CONSTRAINT edges_kind_chk")
    conn.execute(
        "ALTER TABLE cm.edges ADD CONSTRAINT edges_kind_chk CHECK ("
        "src_kind IN ('file','symbol','db_table','db_column','definition','concept','zz_suelto') AND "
        "dst_kind IN ('file','symbol','db_table','db_column','definition','concept')) NOT VALID"
    )
    errores = cg.incoherencias(cg.extraer(conn))
    assert errores == [
        "cm.edges.edges_kind_chk (src_kind) frente a cm.node_types: "
        "'zz_suelto' no está en el catálogo"
    ]


# ---------------------------------------------------------------------------
# Funciones puras
# ---------------------------------------------------------------------------
def test_valores_permitidos_lee_el_check_normalizado() -> None:
    definicion = (
        "CHECK (((src_kind = ANY (ARRAY['file'::text, 'it''s'::text])) AND "
        "(dst_kind = ANY (ARRAY['symbol'::text]))))"
    )
    assert cg.valores_permitidos(definicion, "src_kind") == ["file", "it's"]
    assert cg.valores_permitidos(definicion, "dst_kind") == ["symbol"]
    assert cg.valores_permitidos(definicion, "otra") == []
    assert cg.valores_permitidos("", "tipo") == []


def test_un_kind_con_dos_tablas_o_sin_trigger_de_baja_se_detecta() -> None:
    modelo = {
        "tipos_nodo": [{"kind": "file"}],
        "tipos_arista": [{"tipo": "CALLS"}],
        "respaldo": {
            "file": {"tablas": ["cm.files", "cm.otra"], "triggers": ["nodes_sync_ins"]},
        },
        "arista": {"check_tipo": ["CALLS"], "check_src": ["file"], "check_dst": ["file"]},
    }
    errores = cg.incoherencias(modelo)
    assert len(errores) == 2
    assert "lo respaldan 2 tablas" in errores[0]
    assert "le falta un trigger" in errores[1]
