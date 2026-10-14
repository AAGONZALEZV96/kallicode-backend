import os

import psycopg
import pytest


def normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


def test_indices_optimizacion_grafo():
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
        t.relname AS tabla,
        c.relname AS indice,
        i.indisvalid AS es_valido
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    JOIN pg_class t ON t.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    WHERE n.nspname = 'cm'
      AND c.relname IN (
          'idx_repo_snapshots_tenant_commit_estado',
          'idx_repo_snapshots_ultimo_completo',
          'idx_symbols_tenant_file_id',
          'idx_files_tenant_path_sha'
      );
    """

    with conn, conn.cursor() as cur:
        cur.execute(query)
        rows = {r[1]: (r[0], r[2]) for r in cur.fetchall()}

    esperados = [
        "idx_repo_snapshots_tenant_commit_estado",
        "idx_repo_snapshots_ultimo_completo",
        "idx_symbols_tenant_file_id",
        "idx_files_tenant_path_sha",
    ]

    for nombre in esperados:
        assert nombre in rows, f"Falta el índice {nombre} en el esquema cm"
        _, es_valido = rows[nombre]
        assert es_valido, f"El índice {nombre} no es válido en PostgreSQL"
