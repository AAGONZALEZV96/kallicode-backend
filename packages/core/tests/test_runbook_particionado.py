"""Validación automatizada de consultas y sentencias del Runbook de Particionado.

Ticket KC-DB-07-6.
"""

import os
from pathlib import Path
from typing import Any

import psycopg
import pytest

RUNBOOK_PATH = Path("docs/runbooks/particionado_operaciones.md")


def normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


def obtener_conexion() -> psycopg.Connection[Any]:
    raw_url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@localhost:5432/kallicode_test"
    )
    return psycopg.connect(normalizar_dsn(raw_url), autocommit=True)


def test_existencia_y_contenido_minimo_runbook():
    assert RUNBOOK_PATH.exists(), f"El archivo {RUNBOOK_PATH} no existe"
    contenido = RUNBOOK_PATH.read_text(encoding="utf-8")
    assert "DETACH PARTITION" in contenido
    assert "REINDEX TABLE CONCURRENTLY" in contenido
    assert "core.retention_policies" in contenido
    assert "pg_inherits" in contenido


def test_sintaxis_consultas_monitoreo_runbook():
    try:
        conn = obtener_conexion()
    except Exception as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")

    query_particiones = """
    SELECT 
        nmsp_parent.nspname AS parent_schema,
        parent.relname AS parent_table,
        nmsp_child.nspname AS child_schema,
        child.relname AS child_partition,
        pg_get_expr(child.relpartbound, child.oid) AS partition_bounds
    FROM pg_inherits
    JOIN pg_class parent ON pg_inherits.inhparent = parent.oid
    JOIN pg_class child ON pg_inherits.inhrelid = child.oid
    JOIN pg_namespace nmsp_parent ON nmsp_parent.oid = parent.relnamespace
    JOIN pg_namespace nmsp_child ON nmsp_child.oid = child.relnamespace
    WHERE parent.relkind = 'p'
    LIMIT 5;
    """

    with conn, conn.cursor() as cur:
        cur.execute(query_particiones)
        cur.fetchall()
