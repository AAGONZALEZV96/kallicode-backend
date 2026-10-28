import os

import psycopg
import pytest

from packages.core.benchmarks.benchmark_vec_partitioning import (
    DIMENSION,
    generar_vector_sintetico,
    normalizar_dsn,
)


def test_generador_vector_sintetico_dimension():
    vec_str = generar_vector_sintetico(dim=DIMENSION)
    assert vec_str.startswith("[")
    assert vec_str.endswith("]")
    elementos = vec_str[1:-1].split(",")
    assert len(elementos) == DIMENSION


def test_estructuras_benchmark_conexion():
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
        cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector';")
        ext = cur.fetchone()
        assert ext is not None, "La extensión pgvector debe estar habilitada"
