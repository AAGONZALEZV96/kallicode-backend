import argparse
import os
import random
import time
from typing import Any

import psycopg


def normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


def generar_vector_unitario(dimension: int = 1024) -> list[float]:
    v = [random.gauss(0, 1) for _ in range(dimension)]
    norma = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / norma for x in v]


def ejecutar_benchmark(
    num_vectores: int = 200,
    k_vecinos: int = 10,
    ef_search: int = 64,
    tenant_id: str = "org_benchmark",
) -> dict[str, Any]:
    raw_url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@localhost:5432/kallicode_test"
    )
    conn = psycopg.connect(normalizar_dsn(raw_url), autocommit=True)

    try:
        with conn.cursor() as cur:
            # 1. Asegurar organización de prueba
            cur.execute(
                """
                INSERT INTO core.organizations (id, nombre)
                VALUES (%s, 'Org Benchmark')
                ON CONFLICT (id) DO NOTHING;
                """,
                (tenant_id,),
            )

            # 2. Limpiar datos previos del tenant
            cur.execute(
                "DELETE FROM vec.definition_embeddings WHERE tenant_id = %s;",
                (tenant_id,),
            )
            cur.execute(
                "DELETE FROM cm.nodes WHERE tenant_id = %s;",
                (tenant_id,),
            )

            # 3. Insertar nodos en cm.nodes y embeddings en vec.definition_embeddings
            vectores = [generar_vector_unitario() for _ in range(num_vectores)]
            for i, vec in enumerate(vectores):
                nodo_id = f"node_{i:04d}"
                # Nodo padre en el grafo
                cur.execute(
                    """
                    INSERT INTO cm.nodes (tenant_id, id, kind)
                    VALUES (%s, %s, 'definition')
                    ON CONFLICT (tenant_id, id) DO NOTHING;
                    """,
                    (tenant_id, nodo_id),
                )
                # Registro vectorial con capa válida en de_capa_chk ('definition')
                cur.execute(
                    """
                    INSERT INTO vec.definition_embeddings (
                        id, tenant_id, nodo_ref, capa, nombre, descripcion, embedding, texto_hash
                    ) VALUES (
                        %s, %s, %s, 'definition', %s, 'benchmark recall', %s::vector, %s
                    );
                    """,
                    (
                        f"emb_bench_{i:04d}",
                        tenant_id,
                        nodo_id,
                        f"sym_{i}",
                        str(vec),
                        f"hash_{i}",
                    ),
                )

            # Vector de consulta aleatorio
            query_vec = str(generar_vector_unitario())

            # 4. Búsqueda exacta (Ground Truth) desactivando índices
            cur.execute("SET LOCAL enable_indexscan = off;")
            cur.execute("SET LOCAL enable_bitmapscan = off;")
            t0 = time.perf_counter()
            cur.execute(
                """
                SELECT id FROM vec.definition_embeddings
                WHERE tenant_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s;
                """,
                (tenant_id, query_vec, k_vecinos),
            )
            ground_truth = {row[0] for row in cur.fetchall()}
            duracion_exacta = time.perf_counter() - t0

            # 5. Búsqueda HNSW aproximada
            cur.execute("SET LOCAL enable_indexscan = on;")
            cur.execute("SET LOCAL enable_bitmapscan = on;")
            cur.execute(f"SET LOCAL hnsw.ef_search = {ef_search};")
            t1 = time.perf_counter()
            cur.execute(
                """
                SELECT id FROM vec.definition_embeddings
                WHERE tenant_id = %s
                ORDER BY embedding <=> %s::vector
                LIMIT %s;
                """,
                (tenant_id, query_vec, k_vecinos),
            )
            hnsw_result = {row[0] for row in cur.fetchall()}
            duracion_hnsw = time.perf_counter() - t1

            # Calcular Recall@k
            aciertos = len(ground_truth.intersection(hnsw_result))
            recall = aciertos / max(k_vecinos, 1)

            # 6. Limpieza posterior
            cur.execute(
                "DELETE FROM vec.definition_embeddings WHERE tenant_id = %s;",
                (tenant_id,),
            )
            cur.execute(
                "DELETE FROM cm.nodes WHERE tenant_id = %s;",
                (tenant_id,),
            )

            return {
                "num_vectores": num_vectores,
                "k": k_vecinos,
                "ef_search": ef_search,
                "recall": recall,
                "latencia_exacta_ms": duracion_exacta * 1000,
                "latencia_hnsw_ms": duracion_hnsw * 1000,
            }
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark HNSW Recall")
    parser.add_argument("--vectores", type=int, default=150)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--ef-search", type=int, default=64)
    args = parser.parse_args()

    res = ejecutar_benchmark(num_vectores=args.vectores, k_vecinos=args.k, ef_search=args.ef_search)
    print(f"Recall@{res['k']}: {res['recall'] * 100:.1f}%")
    print(f"Latencia HNSW: {res['latencia_hnsw_ms']:.2f} ms")
    print(f"Latencia Exacta: {res['latencia_exacta_ms']:.2f} ms")
