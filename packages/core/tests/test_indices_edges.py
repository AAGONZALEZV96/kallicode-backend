import os

import psycopg
import pytest


def normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


def test_indices_edges_con_include():
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
        indnkeyatts AS num_key_cols,
        indnatts AS total_cols,
        indisvalid AS es_valido
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    JOIN pg_class t ON t.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'cm'
      AND t.relname = 'edges'
      AND c.relname IN ('edges_out_ix', 'edges_in_ix');
    """

    with conn, conn.cursor() as cur:
        cur.execute(query)
        rows = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

    assert "edges_out_ix" in rows, "Falta el índice cm.edges_out_ix"
    assert "edges_in_ix" in rows, "Falta el índice cm.edges_in_ix"

    for nombre in ("edges_out_ix", "edges_in_ix"):
        num_key_cols, total_cols, es_valido = rows[nombre]
        assert es_valido, f"El índice {nombre} no es válido"
        assert num_key_cols == 4, (
            f"{nombre} debe tener 4 columnas clave, tiene {num_key_cols}"
        )
        assert total_cols == 7, (
            f"{nombre} debe tener 7 columnas totales con INCLUDE, tiene {total_cols}"
        )