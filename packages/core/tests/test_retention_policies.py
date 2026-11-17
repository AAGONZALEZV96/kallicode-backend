import os

import psycopg
import pytest


def normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


def test_retention_policies_estructura_y_rls():
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

    with conn, conn.cursor() as cur:
        # 1. Validar existencia de la tabla
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'core' AND table_name = 'retention_policies';
            """
        )
        assert cur.fetchone() is not None, "La tabla core.retention_policies no existe"

        # 2. KC-DB-10-1: la PK es el id; la unicidad por (tenant, entidad) y la de las filas globales van en
        #    índices únicos parciales (las políticas de plataforma tienen tenant_id NULL)
        cur.execute(
            """
            SELECT a.attname
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(con.conkey)
            WHERE con.contype = 'p'
              AND n.nspname = 'core'
              AND c.relname = 'retention_policies'
            ORDER BY array_position(con.conkey, a.attnum);
            """
        )
        pk_cols = [r[0] for r in cur.fetchall()]
        assert pk_cols == ["id"], f"PK inesperada: {pk_cols}"
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'core' AND tablename = 'retention_policies' "
            "AND indexname IN ('retention_policies_tenant_entidad_uq', 'retention_policies_global_entidad_uq') "
            "ORDER BY 1"
        )
        assert [r[0] for r in cur.fetchall()] == [
            "retention_policies_global_entidad_uq",
            "retention_policies_tenant_entidad_uq",
        ]

        # 3. Validar RLS activo y forzado
        cur.execute(
            """
            SELECT relrowsecurity, relforcerowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'core' AND c.relname = 'retention_policies';
            """
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] is True, "RLS debe estar habilitado"
        assert row[1] is True, "RLS debe ser forzado (FORCE ROW LEVEL SECURITY)"
