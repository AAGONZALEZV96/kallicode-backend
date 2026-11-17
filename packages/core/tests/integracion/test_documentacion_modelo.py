"""Documentación del modelo v2 generada desde pg_catalog (KC-DB-12-2).

Criterios de aceptación:
  1. Toda tabla, columna y constraint lleva COMMENT; el CI falla si falta alguno
     (test_toda_tabla_columna_y_constraint_lleva_comment, test_un_objeto_nuevo_sin_comment_se_detecta).
  2. El documento generado se compara en CI con la copia versionada docs/modelo_datos_v2.md
     (test_el_documento_versionado_coincide_con_el_catalogo).
  3. El documento incorpora las siete afirmaciones corregidas de la auditoría, verificadas contra el catálogo
     (test_las_siete_afirmaciones_...), y el mapa de relaciones marca qué claves foráneas son compuestas con
     tenant_id (test_el_mapa_marca_las_fk_compuestas_con_tenant_id).

Si la prueba de coincidencia falla tras añadir una migración, regenera el documento:
    python db/tools/documentar_esquema.py
Las pruebas que crean objetos de prueba lo hacen dentro de una transacción que SIEMPRE se revierte.
"""

from __future__ import annotations

import importlib.util
import os
import re
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest
from psycopg.rows import dict_row

RAIZ = Path(__file__).resolve().parents[4]
HERRAMIENTA = RAIZ / "db" / "tools" / "documentar_esquema.py"
REGENERAR = "Regenera el documento con: python db/tools/documentar_esquema.py"


def _cargar_herramienta() -> ModuleType:
    spec = importlib.util.spec_from_file_location("documentar_esquema", HERRAMIENTA)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


de = _cargar_herramienta()


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _conectar() -> psycopg.Connection[dict[str, Any]]:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    try:
        return psycopg.connect(_normalizar_dsn(url), autocommit=False, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    c = _conectar()
    try:
        fila = c.execute("SELECT to_regproc('core.version_esquema') AS f").fetchone()
        if not fila or fila["f"] is None:
            pytest.skip("migración 067 sin aplicar")
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture(scope="module")
def modelo() -> dict[str, Any]:
    c = _conectar()
    try:
        fila = c.execute("SELECT to_regproc('core.version_esquema') AS f").fetchone()
        if not fila or fila["f"] is None:
            pytest.skip("migración 067 sin aplicar")
        datos: dict[str, Any] = de.extraer(c)
        return datos
    finally:
        c.rollback()
        c.close()


# ---------------------------------------------------------------------------
# Criterio 1: COMMENT en todo
# ---------------------------------------------------------------------------
def test_toda_tabla_columna_y_constraint_lleva_comment(modelo: dict[str, Any]) -> None:
    sin = de.faltantes(modelo)
    assert not sin, (
        f"Faltan COMMENT en {len(sin)} objetos (añádelos en la migración que los crea):\n"
        + "\n".join(f"  - {s}" for s in sin[:50])
    )


def test_un_objeto_nuevo_sin_comment_se_detecta(conn: psycopg.Connection[dict[str, Any]]) -> None:
    conn.execute("SET LOCAL ROLE kallicode_admin")
    conn.execute(
        "CREATE TABLE core.zz_doc_prueba (id text PRIMARY KEY, valor integer CHECK (valor > 0))"
    )
    sin = de.faltantes(de.extraer(conn))
    assert "tabla core.zz_doc_prueba" in sin
    assert "columna core.zz_doc_prueba.valor" in sin
    assert any(s.startswith("constraint core.zz_doc_prueba.zz_doc_prueba_pkey") for s in sin)

    conn.execute("COMMENT ON TABLE core.zz_doc_prueba IS 'Prueba.'")
    conn.execute("COMMENT ON COLUMN core.zz_doc_prueba.id IS 'Identificador.'")
    conn.execute("COMMENT ON COLUMN core.zz_doc_prueba.valor IS 'Valor positivo.'")
    conn.execute("COMMENT ON CONSTRAINT zz_doc_prueba_pkey ON core.zz_doc_prueba IS 'Clave.'")
    conn.execute(
        "COMMENT ON CONSTRAINT zz_doc_prueba_valor_check ON core.zz_doc_prueba IS 'Regla.'"
    )
    assert [s for s in de.faltantes(de.extraer(conn)) if "zz_doc_prueba" in s] == []


# ---------------------------------------------------------------------------
# Criterio 2: el documento versionado coincide con el catálogo
# ---------------------------------------------------------------------------
def test_el_documento_versionado_coincide_con_el_catalogo(modelo: dict[str, Any]) -> None:
    generado = de.renderizar(modelo)
    versionado = de.DOCUMENTO.read_text(encoding="utf-8")
    if generado != versionado:
        import difflib

        diff = list(
            difflib.unified_diff(
                versionado.splitlines(), generado.splitlines(), "versionado", "generado", n=0
            )
        )
        pytest.fail(
            f"docs/modelo_datos_v2.md difiere del catálogo. {REGENERAR}\n" + "\n".join(diff[:40])
        )


def test_el_documento_es_determinista(modelo: dict[str, Any]) -> None:
    assert de.renderizar(modelo) == de.renderizar(modelo)


def test_el_documento_describe_cada_tabla_del_modelo(modelo: dict[str, Any]) -> None:
    texto = de.DOCUMENTO.read_text(encoding="utf-8")
    ausentes = [
        f"{t['esquema']}.{t['tabla']}"
        for t in modelo["tablas"]
        if f"#### `{t['esquema']}.{t['tabla']}`" not in texto
    ]
    assert not ausentes, ausentes


def test_el_alcance_coincide_con_la_instantanea_congelada(modelo: dict[str, Any]) -> None:
    """Las cifras del documento son las de la instantánea de KC-DB-12-1 (mismas tablas y particionadas)."""
    import json

    instantanea = json.loads((RAIZ / "db" / "esquema_v2.snapshot.json").read_text(encoding="utf-8"))
    tablas = instantanea["catalogo"]["tablas"]
    assert len(modelo["tablas"]) == len(tablas), REGENERAR


# ---------------------------------------------------------------------------
# Criterio 3: afirmaciones corregidas y mapa de relaciones
# ---------------------------------------------------------------------------
def test_las_siete_afirmaciones_estan_en_el_documento_y_se_cumplen(modelo: dict[str, Any]) -> None:
    afirmaciones = modelo["afirmaciones"]
    assert len(afirmaciones) == 7
    assert [a["id"] for a in afirmaciones] == [f"A{i}" for i in range(1, 8)]
    incumplidas = [a["id"] for a in afirmaciones if not a["verificada"]]
    assert not incumplidas, f"El catálogo no cumple las afirmaciones {incumplidas}"
    texto = de.DOCUMENTO.read_text(encoding="utf-8")
    for a in afirmaciones:
        assert f"### {a['id']} · {a['tema']}" in texto


def test_una_afirmacion_que_el_catalogo_no_cumple_se_detecta(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    conn.execute("SET LOCAL ROLE kallicode_admin")
    conn.execute("CREATE TABLE core.tenants (id text PRIMARY KEY)")  # contradice A1
    verificadas = {a["id"]: a["verificada"] for a in de.extraer(conn)["afirmaciones"]}
    assert verificadas["A1"] is False
    assert all(v for k, v in verificadas.items() if k != "A1")


def test_el_mapa_marca_las_fk_compuestas_con_tenant_id(
    conn: psycopg.Connection[dict[str, Any]], modelo: dict[str, Any]
) -> None:
    # Cuenta independiente, directamente en el catálogo, de las FK compuestas con tenant_id.
    esperado = conn.execute(
        """
        SELECT count(*) AS n
          FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE k.contype = 'f' AND k.conparentid = 0 AND NOT c.relispartition
           AND n.nspname = ANY(%s) AND cardinality(k.conkey) > 1
           AND EXISTS (SELECT 1 FROM pg_attribute a
                        WHERE a.attrelid = k.conrelid AND a.attnum = ANY(k.conkey) AND a.attname = 'tenant_id')
        """,
        (list(de.ESQUEMAS),),
    ).fetchone()["n"]
    marcadas = sum(1 for _, k in de._fks(modelo) if k["compuesta_tenant"])
    assert marcadas == esperado > 0

    texto = de.DOCUMENTO.read_text(encoding="utf-8")
    assert texto.count("| **Sí** |") == esperado
    # Una FK compuesta concreta y una simple, para que la marca no sea un conteo vacío.
    fila = next(
        ln
        for ln in texto.splitlines()
        if ln.startswith("| `vec.definition_embeddings` | `tenant_id, nodo_ref`")
    )
    assert "**Sí**" in fila
    simple = next(
        ln
        for ln in texto.splitlines()
        if ln.startswith("| `core.users` | `tenant_id` | `core.organizations`")
    )
    assert "| No |" in simple


def test_el_documento_lista_los_chequeos_de_integridad(
    conn: psycopg.Connection[dict[str, Any]], modelo: dict[str, Any]
) -> None:
    esperados = [
        r["chequeo"] for r in conn.execute("SELECT chequeo FROM core.validar_integridad()")
    ]
    assert [c["chequeo"] for c in modelo["chequeos"]] == sorted(esperados)
    assert len(esperados) >= 26


# ---------------------------------------------------------------------------
# Herramienta
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("definicion", "columna", "esperado"),
    [
        ("CHECK ((estado = ANY (ARRAY['a'::text, 'b c'::text])))", "estado", ["a", "b c"]),
        ("CHECK (((gate IS NULL) OR (gate = ANY (ARRAY[1, 2, 3]))))", "gate", ["1", "2", "3"]),
        ("CHECK ((retention_days > 0))", "retention_days", None),
        ("CHECK ((x = ANY (ARRAY['o''k'::text])))", "x", ["o'k"]),
    ],
)
def test_valores_permitidos_de_un_check(definicion: str, columna: str, esperado: Any) -> None:
    assert de._valores_permitidos(definicion, columna) == esperado


def test_las_celdas_no_rompen_la_tabla_markdown() -> None:
    assert "|" not in re.sub(r"\\\|", "", de._c("a | b\nc"))
    assert de._c(None) == ""
    assert de._codigo("x`y") == "`x'y`"
