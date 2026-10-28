import os
from typing import Any

import psycopg
import pytest


def normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


@pytest.fixture
def conn() -> psycopg.Connection[Any]:
    raw_url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@localhost:5432/kallicode_test"
    )
    db_url = normalizar_dsn(raw_url)
    try:
        connection = psycopg.connect(db_url, autocommit=True)
    except Exception as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    yield connection
    connection.close()


def test_tablas_vectoriales_particionadas_por_tenant(
    conn: psycopg.Connection[Any],
) -> None:
    query = """
    SELECT
        c.relname AS tabla,
        c.relkind AS tipo,
        partstrat AS estrategia
    FROM pg_partitioned_table p
    JOIN pg_class c ON c.oid = p.partrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'vec'
      AND c.relname IN ('definition_embeddings', 'doc_chunks');
    """
    with conn.cursor() as cur:
        cur.execute(query)
        filas = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    assert "definition_embeddings" in filas, "Falta particionar vec.definition_embeddings"
    assert filas["definition_embeddings"][0] == "p", (
        "vec.definition_embeddings debe ser relkind 'p'"
    )
    assert filas["definition_embeddings"][1] == "l", (
        "vec.definition_embeddings debe usar particionado LIST ('l')"
    )

    assert "doc_chunks" in filas, "Falta particionar vec.doc_chunks"
    assert filas["doc_chunks"][0] == "p", "vec.doc_chunks debe ser relkind 'p'"
    assert filas["doc_chunks"][1] == "l", "vec.doc_chunks debe usar particionado LIST ('l')"


def test_particiones_default_vectoriales_existen(
    conn: psycopg.Connection[Any],
) -> None:
    query = """
    SELECT
        c.relname AS particion
    FROM pg_inherits i
    JOIN pg_class c ON c.oid = i.inhrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'vec'
      AND c.relname IN ('definition_embeddings_default', 'doc_chunks_default');
    """
    with conn.cursor() as cur:
        cur.execute(query)
        particiones = {row[0] for row in cur.fetchall()}

    assert "definition_embeddings_default" in particiones
    assert "doc_chunks_default" in particiones
