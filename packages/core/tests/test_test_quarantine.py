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


def test_tabla_test_quarantine_estructura(conn: psycopg.Connection[Any]) -> None:
    query = """
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_schema = 'core' AND table_name = 'test_quarantine'
    ORDER BY ordinal_position;
    """
    with conn.cursor() as cur:
        cur.execute(query)
        columnas = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    assert "id" in columnas
    assert "tenant_id" in columnas
    assert "test_node_id" in columnas
    assert "motivo" in columnas
    assert columnas["motivo"][1] == "NO"


def test_test_quarantine_rechaza_motivo_vacio(conn: psycopg.Connection[Any]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation), conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.test_quarantine (id, tenant_id, test_node_id, motivo, cuarentenado_por)
            VALUES ('tq_test_01', 'org_sistema', 'test_node_x', '   ', 'usr_mario');
            """
        )
