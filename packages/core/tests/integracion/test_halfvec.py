"""Representación vectorial: float4 vigente y conversión a halfvec ensayada (KC-DB-08-3, DB-F31, ADR-022).

El ADR-022 mantiene vector(1024): con el HNSW halfvec pierde 4-5 puntos de recall@10 (criterio de adopción: < 1).
Aquí se fija lo que queda en la base (columnas vector(1024), HNSW vector_cosine_ops, catálogo con su clase de
operadores) y se ENSAYA, con filas y dentro de una transacción que se deshace (DDL transaccional), el script
preparado db/propuestas/halfvec/aplicar_halfvec.sql y su reversión, para que adoptar halfvec algún día sea
una operación conocida y no una improvisación. El recall lo mide packages/core/tests/bench_halfvec.py (CI nocturno).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
APLICAR = RAIZ / "db" / "propuestas" / "halfvec" / "aplicar_halfvec.sql"
REVERTIR = RAIZ / "db" / "propuestas" / "halfvec" / "revertir_halfvec.sql"
TABLAS = {
    "ticket_embeddings": "ticket_embeddings_hnsw_ix",
    "doc_chunks": "doc_chunks_hnsw_ix",
    "definition_embeddings": "definition_embeddings_hnsw_ix",
}
VECTOR = "[" + ",".join(["0.03125"] * 1024) + "]"


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


@pytest.fixture
def admin() -> Iterator[psycopg.Connection]:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    try:
        c = psycopg.connect(_normalizar_dsn(url), autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    c.execute("SELECT '[1,2,3]'::vector")
    c.commit()
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _tipo(c: psycopg.Connection, tabla: str) -> str:
    fila = c.execute(
        "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
        "WHERE attrelid = %s::regclass AND attname = 'embedding' AND NOT attisdropped",
        (f"vec.{tabla}",),
    ).fetchone()
    assert fila is not None
    return str(fila[0])


def _opclases_hnsw(c: psycopg.Connection, tabla: str) -> set[str]:
    """Clases de operadores de los HNSW válidos de la tabla y de sus particiones."""
    return {
        str(r[0])
        for r in c.execute(
            """SELECT oc.opcname
                 FROM pg_index i
                 JOIN pg_class ic ON ic.oid = i.indexrelid
                 JOIN pg_am am ON am.oid = ic.relam AND am.amname = 'hnsw'
                 JOIN pg_opclass oc ON oc.oid = i.indclass[0]
                WHERE i.indisvalid
                  AND (i.indrelid = %s::regclass
                       OR i.indrelid IN (SELECT relid FROM pg_partition_tree(%s::regclass)))""",
            (f"vec.{tabla}", f"vec.{tabla}"),
        ).fetchall()
    }


def _sql(ruta: Path) -> bytes:
    """El script sin metacomandos de psql ni su BEGIN/COMMIT (la transacción la pone la prueba)."""
    lineas = [
        ln
        for ln in ruta.read_text(encoding="utf-8").splitlines()
        if not ln.startswith("\\") and ln.strip().upper() not in {"BEGIN;", "COMMIT;"}
    ]
    return "\n".join(lineas).encode()


def _cargar_filas(c: psycopg.Connection) -> dict[str, int]:
    """Una fila por colección (sin comprobar claves foráneas: la prueba solo mira el tipo y el índice)."""
    c.execute("SET LOCAL session_replication_role = replica")
    t = f"t_{uuid.uuid4().hex[:8]}"
    c.execute(
        "INSERT INTO vec.ticket_embeddings (ticket_id, tenant_id, embedding, texto_hash) VALUES (%s, %s, %s::vector, 'h')",
        (f"tk_{t}", t, VECTOR),
    )
    c.execute(
        "INSERT INTO vec.doc_chunks (id, tenant_id, document_id, chunk_index, content, embedding, texto_hash) "
        "VALUES (%s, %s, %s, 0, 'c', %s::vector, 'h')",
        (f"dc_{t}", t, f"doc_{t}", VECTOR),
    )
    c.execute(
        "INSERT INTO vec.definition_embeddings (id, tenant_id, nodo_ref, capa, nombre, descripcion, embedding, texto_hash) "
        "VALUES (%s, %s, %s, 'symbol', 'n', 'd', %s::vector, 'h')",
        (f"de_{t}", t, f"nodo_{t}", VECTOR),
    )
    return {tabla: _contar(c, tabla) for tabla in TABLAS}


def _contar(c: psycopg.Connection, tabla: str) -> int:
    fila = c.execute(f"SELECT count(*) FROM vec.{tabla}").fetchone()
    assert fila is not None
    return int(fila[0])


# --- lo que hay en la base: float4 (decisión del ADR-022) ----------------------------------------------
@pytest.mark.parametrize("tabla", list(TABLAS))
def test_las_colecciones_siguen_siendo_vector_1024(admin: psycopg.Connection, tabla: str) -> None:
    assert _tipo(admin, tabla) == "vector(1024)"
    assert _opclases_hnsw(admin, tabla) == {"vector_cosine_ops"}


def test_el_catalogo_guarda_la_clase_de_operadores(admin: psycopg.Connection) -> None:
    filas = {
        str(r[0]): str(r[1])
        for r in admin.execute(
            "SELECT indice, opclase FROM vec.hnsw_catalogo WHERE indice = ANY(%s)",
            (list(TABLAS.values()),),
        ).fetchall()
    }
    assert filas == dict.fromkeys(TABLAS.values(), "vector_cosine_ops")


def test_el_catalogo_rechaza_una_clase_de_operadores_desconocida(admin: psycopg.Connection) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("UPDATE vec.hnsw_catalogo SET opclase = 'sparsevec_l2_ops'")


def test_el_script_de_indices_concurrentes_lee_la_clase_de_operadores() -> None:
    texto = (RAIZ / "db" / "06_indices_concurrentes.sql").read_text(encoding="utf-8")
    assert "vector_cosine_ops" not in texto
    assert texto.count("o.opclase") == 3


# --- ensayo de la conversión preparada -------------------------------------------------------------------
def test_ensayo_aplicar_y_revertir_halfvec_con_filas(admin: psycopg.Connection) -> None:
    admin.execute("SET LOCAL lock_timeout = '10s'")
    antes = _cargar_filas(admin)
    assert all(n >= 1 for n in antes.values())

    admin.execute(_sql(APLICAR))  # type: ignore[arg-type]
    for tabla, indice in TABLAS.items():
        assert _tipo(admin, tabla) == "halfvec(1024)"
        assert _opclases_hnsw(admin, tabla) == {"halfvec_cosine_ops"}
        assert _contar(admin, tabla) == antes[tabla], "la conversión no pierde filas"
        assert admin.execute(
            "SELECT valido FROM vec.v_hnsw_estado WHERE indice = %s", (indice,)
        ).fetchone() == (True,)
    assert admin.execute(
        "SELECT count(*) FROM vec.hnsw_catalogo WHERE opclase = 'halfvec_cosine_ops'"
    ).fetchone() == (3,)
    sql = admin.execute("SELECT cm.busqueda_hibrida_sql()").fetchone()
    assert sql is not None and str(sql[0]).count("<=> $1::halfvec") == 6

    # el código actual (CAST(:e AS vector)) sigue usando el índice: el cast vector -> halfvec es implícito
    admin.execute("SET LOCAL enable_seqscan = off")
    for tipo in ("vector", "halfvec"):
        plan = "\n".join(
            r[0]
            for r in admin.execute(
                f"EXPLAIN SELECT ticket_id FROM vec.ticket_embeddings ORDER BY embedding <=> %s::{tipo} LIMIT 5",
                (VECTOR,),
            ).fetchall()
        )
        assert "ticket_embeddings_hnsw_ix" in plan, (tipo, plan)

    admin.execute(_sql(REVERTIR))  # type: ignore[arg-type]
    for tabla in TABLAS:
        assert _tipo(admin, tabla) == "vector(1024)"
        assert _opclases_hnsw(admin, tabla) == {"vector_cosine_ops"}
        assert _contar(admin, tabla) == antes[tabla]
    sql = admin.execute("SELECT cm.busqueda_hibrida_sql()").fetchone()
    assert sql is not None and "halfvec" not in str(sql[0])

    # reaplicable tras revertir, e idempotente
    admin.execute(_sql(APLICAR))  # type: ignore[arg-type]
    admin.execute(_sql(APLICAR))  # type: ignore[arg-type]
    assert _tipo(admin, "doc_chunks") == "halfvec(1024)"

    admin.rollback()
    assert _tipo(admin, "doc_chunks") == "vector(1024)"


def test_el_redondeo_de_float2_tiene_el_error_esperado(admin: psycopg.Connection) -> None:
    """Un valor se guarda con ~3 cifras decimales: la pérdida que mide bench_halfvec.py como «solo el redondeo»."""
    fila = admin.execute("SELECT ('[0.123456,1,2]'::halfvec(3))::text").fetchone()
    assert fila is not None
    primero = float(str(fila[0]).strip("[]").split(",")[0])
    assert primero != 0.123456
    assert abs(primero - 0.123456) < 1e-3
