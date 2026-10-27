"""Script de benchmark comparativo: Tabla vectorial plana vs.

Particionada por LIST (capa). Ticket KC-DB-07-5.
"""

import math
import os
import random
import time
from typing import Any

import psycopg

DIMENSION = 1024
NUM_VECTORES = 1200
CAPAS = ["file", "symbol", "db_table", "db_column", "definition", "concept"]


def generar_vector_sintetico(dim: int = DIMENSION) -> str:
    """Genera un vector normalizado en formato texto para pgvector."""
    raw = [random.gauss(0, 1) for _ in range(dim)]
    norma = math.sqrt(sum(x * x for x in raw)) or 1.0
    return "[" + ",".join(f"{x / norma:.5f}" for x in raw) + "]"


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


def configurar_estructuras(cur: psycopg.Cursor[Any]) -> None:
    cur.execute("CREATE SCHEMA IF NOT EXISTS bench;")
    cur.execute("DROP TABLE IF EXISTS bench.vec_plana CASCADE;")
    cur.execute("DROP TABLE IF EXISTS bench.vec_list CASCADE;")

    # 1. Tabla Plana
    cur.execute(
        f"""
        CREATE TABLE bench.vec_plana (
            id text PRIMARY KEY,
            tenant_id text NOT NULL,
            capa text NOT NULL,
            embedding vector({DIMENSION}) NOT NULL
        );
        """
    )

    # 2. Tabla Particionada por LIST (capa)
    cur.execute(
        f"""
        CREATE TABLE bench.vec_list (
            id text NOT NULL,
            tenant_id text NOT NULL,
            capa text NOT NULL,
            embedding vector({DIMENSION}) NOT NULL,
            PRIMARY KEY (capa, id)
        ) PARTITION BY LIST (capa);
        """
    )

    for capa in CAPAS:
        cur.execute(
            f"""
            CREATE TABLE bench.vec_list_{capa}
            PARTITION OF bench.vec_list FOR VALUES IN ('{capa}');
            """
        )


def poblar_datos(cur: psycopg.Cursor[Any]) -> None:
    print(f"Insertando {NUM_VECTORES} vectores sintéticos...")
    for i in range(NUM_VECTORES):
        capa = CAPAS[i % len(CAPAS)]
        vec = generar_vector_sintetico()
        record_id = f"vec_{i:05d}"
        tenant_id = "org_benchmark_test"

        cur.execute(
            "INSERT INTO bench.vec_plana (id, tenant_id, capa, embedding) VALUES (%s, %s, %s, %s);",
            (record_id, tenant_id, capa, vec),
        )
        cur.execute(
            "INSERT INTO bench.vec_list (id, tenant_id, capa, embedding) VALUES (%s, %s, %s, %s);",
            (record_id, tenant_id, capa, vec),
        )


def crear_indices(cur: psycopg.Cursor[Any]) -> None:
    print("Creando índices HNSW...")
    # Índice HNSW en tabla plana
    cur.execute(
        """
        CREATE INDEX vec_plana_hnsw_ix ON bench.vec_plana
        USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
        """
    )

    # Índices HNSW locales por cada partición
    for capa in CAPAS:
        cur.execute(
            f"""
            CREATE INDEX vec_list_{capa}_hnsw_ix ON bench.vec_list_{capa}
            USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
            """
        )
    cur.execute("ANALYZE bench.vec_plana;")
    cur.execute("ANALYZE bench.vec_list;")


def medir_consultas(cur: psycopg.Cursor[Any], n_queries: int = 50) -> dict[str, float]:
    latencias: dict[str, list[float]] = {
        "plana_con_filtro": [],
        "list_con_pruning": [],
        "plana_global": [],
        "list_global_append": [],
    }

    vectores_query = [generar_vector_sintetico() for _ in range(n_queries)]

    for q_vec in vectores_query:
        # A. Búsqueda con filtro de capa
        t0 = time.perf_counter()
        cur.execute(
            """
            SELECT id FROM bench.vec_plana
            WHERE capa = 'symbol'
            ORDER BY embedding <=> %s LIMIT 10;
            """,
            (q_vec,),
        )
        cur.fetchall()
        latencias["plana_con_filtro"].append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        cur.execute(
            """
            SELECT id FROM bench.vec_list
            WHERE capa = 'symbol'
            ORDER BY embedding <=> %s LIMIT 10;
            """,
            (q_vec,),
        )
        cur.fetchall()
        latencias["list_con_pruning"].append((time.perf_counter() - t0) * 1000)

        # B. Búsqueda global (sin filtro de capa)
        t0 = time.perf_counter()
        cur.execute(
            """
            SELECT id FROM bench.vec_plana
            ORDER BY embedding <=> %s LIMIT 10;
            """,
            (q_vec,),
        )
        cur.fetchall()
        latencias["plana_global"].append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        cur.execute(
            """
            SELECT id FROM bench.vec_list
            ORDER BY embedding <=> %s LIMIT 10;
            """,
            (q_vec,),
        )
        cur.fetchall()
        latencias["list_global_append"].append((time.perf_counter() - t0) * 1000)

    res: dict[str, float] = {}
    for clave, valores in latencias.items():
        valores.sort()
        res[f"{clave}_p50"] = round(valores[len(valores) // 2], 3)
        res[f"{clave}_p95"] = round(valores[int(len(valores) * 0.95)], 3)
    return res


def ejecutar_benchmark() -> dict[str, float]:
    with obtener_conexion() as conn, conn.cursor() as cur:
        configurar_estructuras(cur)
        poblar_datos(cur)
        crear_indices(cur)
        metricas = medir_consultas(cur)
    return metricas


if __name__ == "__main__":
    metricas = ejecutar_benchmark()
    print("=== RESULTADOS DEL BENCHMARK (ms) ===")
    for k, v in metricas.items():
        print(f"{k}: {v} ms")
