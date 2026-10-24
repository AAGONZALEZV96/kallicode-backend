import os

import psycopg
import pytest


def normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


def test_indice_hnsw_valido_y_concurrente():
    raw_url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@localhost:5432/kallicode_test"
    )
    db_url = normalizar_dsn(raw_url)

    try:
        conn = psycopg.connect(db_url)
    except Exception as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")

    query = """
    SELECT
        c.relname AS index_name,
        am.amname AS access_method,
        i.indisvalid AS es_valido
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    JOIN pg_class t ON t.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    JOIN pg_am am ON am.oid = c.relam
    WHERE n.nspname = 'vec'
      AND t.relname = 'definition_embeddings'
      AND c.relname = 'definition_embeddings_hnsw_ix';
    """

    with conn, conn.cursor() as cur:
        try:
            cur.execute(
                "SET hnsw.iterative_scan = 'relaxed_order';"
            )  # 'relaxed' no es un valor válido
        except psycopg.Error:
            conn.rollback()

        cur.execute(query)
        row = cur.fetchone()

    assert row is not None, "Falta el índice vec.definition_embeddings_hnsw_ix"
    assert row[1] == "hnsw", f"El método de acceso debe ser hnsw, es {row[1]}"
    assert row[2] is True, (
        "El índice HNSW no quedó marcado como válido tras la creación concurrente"
    )
