"""Benchmark de la representación vectorial: vector (float4) vs halfvec (float2) vs cuantización binaria
(KC-DB-08-3, DB-F31).

Las tres colecciones (vec.ticket_embeddings, vec.doc_chunks, vec.definition_embeddings) guardan vector(1024)
en float4: 4 KB por fila más el grafo HNSW. Este benchmark compara, con el mismo corpus, las mismas consultas y
el índice HNSW real (m = 16, ef_construction = 64, coseno), tres representaciones:

  * vector   float4, el estado actual (línea base: el top-10 EXACTO se calcula sin índice sobre float4).
  * halfvec  float2: la mitad de bytes por vector, índice con halfvec_cosine_ops.
  * binaria  cuantización binaria (bit, 1 bit por dimensión) para un primer paso rápido en el HNSW y
             REORDENACIÓN EXACTA de los candidatos con el vector completo (búsqueda en dos fases).

Mide recall@10, latencia p95 y el tamaño de tabla y de índice, a los dos ef_search que importan (400, el de
Diseño en vec.parametros_busqueda, y 100). Además mide halfvec SIN índice (solo el redondeo a float2) para
separar lo que cuesta el formato de lo que cuesta el grafo HNSW.

Decisión (ADR-022): se adopta halfvec en una colección si la pérdida de recall@10 frente a float4 es inferior
a 1 punto. Resultado actual: NO se cumple en ninguna (halfvec con HNSW pierde 4-5 puntos aunque el redondeo
solo cuesta ~0,1), así que se mantiene vector(1024). Lo que vigilan los tests (CI nocturno):
  * la representación VIGENTE (float4) conserva el recall@10 que exige la búsqueda de Diseño;
  * la alternativa viable (binaria + reordenación exacta x200) sigue igualando a float4 con un índice mucho menor;
  * el redondeo a float2 por sí solo sigue costando menos de un punto;
  * un aviso («REABRIR ADR-022») cuando halfvec con HNSW pase el criterio: no falla, avisa de que cambió la
    situación (nueva versión de pgvector, otro corpus).

Corpus. Los vectores semilla son REALES: embeddings BGE-M3 (1024 dim, el modelo de producción) de textos del
propio repositorio, una categoría por colección: docs/*.md -> doc_chunks, docstrings de Python ->
definition_embeddings y comentarios en lenguaje natural de las migraciones SQL -> ticket_embeddings (lo más
parecido a un ticket que hay en el repo). Se guardan en packages/core/tests/data/corpus_bge_m3.npz y se
regeneran con packages/core/tests/data/generar_corpus_bge_m3.py. Como son pocos (centenares por colección),
se AMPLÍAN a N filas mezclando pares de semillas con ruido: la distribución (covarianza, concentración de
similitudes) es la del modelo real, pero los vecinos cercanos de las filas generadas son sintéticos. Las
consultas son mezclas nuevas con la misma regla. Es un corpus SEMI-REAL: los datos de un cliente podrían
comportarse distinto (ADR-022).

    uv run pytest packages/core/tests/bench_halfvec.py -v          # umbral (CI nocturno)
    uv run python packages/core/tests/bench_halfvec.py --tabla     # imprime la comparativa en Markdown

Requiere KC_DATABASE_URL_MIGRATIONS (superusuario; crea el corpus en un esquema temporal).
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import psycopg
import pytest

pytestmark = pytest.mark.benchmark

DIM = 1024
N = int(os.getenv("KC_BENCH_HALFVEC_N", "50000"))  # filas por colección
N_CONSULTAS = 200
K = 10
SEMILLA = 20261113
ESQUEMA = "bench_hv"
TRABAJADORES = int(
    os.getenv("KC_BENCH_HALFVEC_WORKERS", "4")
)  # construcción paralela de los índices
EF_SEARCH = 400  # el de vec.parametros_busqueda para Diseño (recall@10 exacto), KC-DB-08-1
SOBREMUESTREO = (50, 200)  # candidatos del primer paso binario, antes de reordenar
UMBRAL_PERDIDA = 0.01
UMBRAL_RECALL_FLOAT4 = 0.97  # recall@10 mínimo de float4 a EF_SEARCH (vec.parametros_busqueda: Diseño exige 0,90)  # criterio de adopción: pérdida de recall@10 inferior a 1 punto
DATOS = Path(__file__).parent / "data" / "corpus_bge_m3.npz"
# colección de vec.* -> categoría de semillas
COLECCIONES = {
    "ticket_embeddings": "tickets",
    "doc_chunks": "docs",
    "definition_embeddings": "definiciones",
}


def _dsn() -> str:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _binario(v: np.ndarray, tipo: str) -> bytes:
    if tipo == "halfvec":
        return struct.pack(">HH", DIM, 0) + v.astype(">f2").tobytes()
    return struct.pack(">HH", DIM, 0) + v.astype(">f4").tobytes()


def _texto(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def cargar_semillas(categoria: str) -> np.ndarray:
    if not DATOS.exists():
        pytest.skip(f"falta el corpus real {DATOS} (ver el docstring)")
    m = np.load(DATOS)[categoria].astype(np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def ampliar(semillas: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    """Mezcla pares de vectores reales (peso aleatorio) y añade ruido gaussiano pequeño."""
    s = len(semillas)
    i = rng.integers(0, s, n)
    j = rng.integers(0, s, n)
    a = rng.beta(0.6, 0.6, (n, 1)).astype(np.float32)
    v = (
        a * semillas[i]
        + (1 - a) * semillas[j]
        + 0.03 * rng.standard_normal((n, DIM)).astype(np.float32) / np.sqrt(DIM)
    )
    return v / np.linalg.norm(v, axis=1, keepdims=True)


@dataclass
class Fila:
    nombre: str
    recall: float
    p95_ms: float
    tabla: int
    indice: int


def construir(conn: psycopg.Connection, corpus: np.ndarray) -> None:
    conn.execute(f"DROP SCHEMA IF EXISTS {ESQUEMA} CASCADE")
    conn.execute(f"CREATE SCHEMA {ESQUEMA}")
    for tabla, tipo in (("t_vector", "vector"), ("t_halfvec", "halfvec")):
        conn.execute(
            f"CREATE TABLE {ESQUEMA}.{tabla} (id int PRIMARY KEY, embedding {tipo}({DIM}) NOT NULL)"
        )
        with (
            conn.cursor() as cur,
            cur.copy(f"COPY {ESQUEMA}.{tabla} (id, embedding) FROM STDIN (FORMAT BINARY)") as cp,
        ):
            cp.set_types(["int4", "bytea"])
            for k, v in enumerate(corpus):
                cp.write_row((k, _binario(v, tipo)))
    # Cuantización binaria: se guarda el vector completo (para reordenar) y el índice es sobre su versión de 1 bit.
    conn.execute(
        f"CREATE TABLE {ESQUEMA}.t_binaria (id int PRIMARY KEY, embedding vector({DIM}) NOT NULL)"
    )
    conn.execute(f"INSERT INTO {ESQUEMA}.t_binaria SELECT id, embedding FROM {ESQUEMA}.t_vector")
    conn.execute("SET maintenance_work_mem = '2GB'")
    conn.execute(f"SET max_parallel_maintenance_workers = {TRABAJADORES}")
    t = {}
    for nombre, ddl in (
        (
            "vector",
            f"CREATE INDEX ON {ESQUEMA}.t_vector USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)",
        ),
        (
            "halfvec",
            f"CREATE INDEX ON {ESQUEMA}.t_halfvec USING hnsw (embedding halfvec_cosine_ops) WITH (m = 16, ef_construction = 64)",
        ),
        (
            "binaria",
            f"CREATE INDEX ON {ESQUEMA}.t_binaria USING hnsw ((binary_quantize(embedding)::bit({DIM})) bit_hamming_ops) "
            "WITH (m = 16, ef_construction = 64)",
        ),
    ):
        t0 = time.time()
        conn.execute(ddl)
        t[nombre] = time.time() - t0
    conn.execute(f"ANALYZE {ESQUEMA}.t_vector, {ESQUEMA}.t_halfvec, {ESQUEMA}.t_binaria")
    conn.commit()
    print(
        "construcción de índices (s):",
        {k: round(v) for k, v in t.items()},
        file=sys.stderr,
        flush=True,
    )


def _conectar() -> psycopg.Connection:
    """Sin consultas preparadas automáticamente: el plan del cálculo exacto (índices desactivados) se
    reutilizaría para las consultas medidas (mismo texto) y todas serían un barrido secuencial."""
    conn = psycopg.connect(_dsn(), autocommit=False)
    conn.prepare_threshold = None
    return conn


def _tamanos(conn: psycopg.Connection, tabla: str) -> tuple[int, int]:
    fila = conn.execute(
        "SELECT pg_table_size(%s::regclass), pg_indexes_size(%s::regclass) - pg_relation_size(%s::regclass || '_pkey')",
        (f"{ESQUEMA}.{tabla}", f"{ESQUEMA}.{tabla}", f"{ESQUEMA}.{tabla}"),
    ).fetchone()
    return int(fila[0]), int(fila[1])


def exactos(conn: psycopg.Connection, consultas: np.ndarray) -> list[list[int]]:
    """Top-10 exacto sobre float4 sin índice: la verdad contra la que se mide el recall."""
    out = []
    conn.execute("SET enable_indexscan = off; SET enable_bitmapscan = off")
    for q in consultas:
        out.append(
            [
                r[0]
                for r in conn.execute(
                    f"SELECT id FROM {ESQUEMA}.t_vector ORDER BY embedding <=> %s::vector LIMIT {K}",
                    (_texto(q),),
                ).fetchall()
            ]
        )
    conn.execute("RESET enable_indexscan; RESET enable_bitmapscan")
    return out


def medir(
    conn: psycopg.Connection,
    nombre: str,
    consultas: np.ndarray,
    verdad: list[list[int]],
    sql: str,
    ajustes: str = "",
    ef: int = EF_SEARCH,
    exacto: bool = False,
) -> Fila:
    # exacto: sin índice (recorre todo con el tipo de la tabla): separa la pérdida por el redondeo a float2
    # de la que añade el grafo HNSW (el grafo de float4 y el de halfvec son distintos y el azar de su
    # construcción mueve el recall unos puntos).
    if exacto:
        conn.execute("SET enable_indexscan = off")
        conn.execute("SET enable_bitmapscan = off")
    else:
        conn.execute("SET enable_seqscan = off")
    conn.execute(f"SET hnsw.ef_search = {ef}")
    if ajustes:
        conn.execute(ajustes)
    aciertos, tiempos = 0, []
    for q, ref in zip(consultas, verdad, strict=True):
        t0 = time.perf_counter()
        ids = [r[0] for r in conn.execute(sql, {"q": _texto(q)}).fetchall()]
        tiempos.append((time.perf_counter() - t0) * 1000)
        aciertos += len(set(ids) & set(ref))
    tiempos.sort()
    tabla = {"vector": "t_vector", "halfvec": "t_halfvec"}.get(nombre.split()[0], "t_binaria")
    tam_tabla, tam_indice = _tamanos(conn, tabla)
    conn.execute("RESET enable_seqscan")
    conn.execute("RESET enable_indexscan")
    conn.execute("RESET enable_bitmapscan")
    conn.execute("RESET hnsw.ef_search")
    return Fila(
        nombre,
        aciertos / (len(consultas) * K),
        tiempos[int(len(tiempos) * 0.95) - 1],
        tam_tabla,
        tam_indice,
    )


def consultas_y_verdad(
    conn: psycopg.Connection, semillas: np.ndarray
) -> tuple[np.ndarray, list[list[int]]]:
    rng = np.random.default_rng(SEMILLA + 1)
    consultas = ampliar(semillas, N_CONSULTAS, rng)  # mezclas nuevas: no están en el corpus
    return consultas, exactos(conn, consultas)


def comparativa(
    conn: psycopg.Connection, consultas: np.ndarray, verdad: list[list[int]]
) -> list[Fila]:
    sql_v = f"SELECT id FROM {ESQUEMA}.t_vector ORDER BY embedding <=> %(q)s::vector LIMIT {K}"
    sql_h = f"SELECT id FROM {ESQUEMA}.t_halfvec ORDER BY embedding <=> %(q)s::halfvec LIMIT {K}"
    filas = [
        medir(conn, "vector (float4)", consultas, verdad, sql_v),
        medir(conn, "halfvec (float2)", consultas, verdad, sql_h),
        medir(
            conn,
            "halfvec exacto, sin índice (solo el redondeo)",
            consultas,
            verdad,
            sql_h,
            exacto=True,
        ),
        medir(conn, "vector (float4), ef_search=100", consultas, verdad, sql_v, ef=100),
        medir(conn, "halfvec (float2), ef_search=100", consultas, verdad, sql_h, ef=100),
    ]
    for m in SOBREMUESTREO:
        filas.append(
            medir(
                conn,
                f"binaria + reordenación exacta (x{m})",
                consultas,
                verdad,
                f"SELECT id FROM (SELECT id, embedding FROM {ESQUEMA}.t_binaria "
                f"ORDER BY binary_quantize(embedding)::bit({DIM}) <~> binary_quantize(%(q)s::vector) LIMIT {m}) c "
                f"ORDER BY embedding <=> %(q)s::vector LIMIT {K}",
                ajustes=f"SET hnsw.ef_search = {max(EF_SEARCH, m)}",
            )
        )
    return filas


def tabla_markdown(filas: list[Fila], n: int) -> str:
    base = filas[0]
    out = [
        "| Representación | recall@10 | pérdida vs float4 | p95 (ms) | tabla | índice | bytes/fila (tabla+índice) |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in filas:
        out.append(
            f"| {f.nombre} | {f.recall:.3f} | {100 * (base.recall - f.recall):+.1f} pts | {f.p95_ms:.1f} | "
            f"{f.tabla / 2**20:.0f} MB | {f.indice / 2**20:.0f} MB | {(f.tabla + f.indice) / n:,.0f} |".replace(
                ",", "."
            )
        )
    return "\n".join(out)


def evaluar_coleccion(conn: psycopg.Connection, coleccion: str) -> list[Fila]:
    semillas = cargar_semillas(COLECCIONES[coleccion])
    construir(conn, ampliar(semillas, N, np.random.default_rng(SEMILLA)))
    consultas, verdad = consultas_y_verdad(conn, semillas)
    return comparativa(conn, consultas, verdad)


@pytest.fixture(scope="module")
def resultados():
    """{coleccion: [Fila vector, Fila halfvec, Fila binaria x50, Fila binaria x200]} (tarda: construye 9 índices)."""
    conn = _conectar()
    try:
        yield {c: evaluar_coleccion(conn, c) for c in COLECCIONES}
    finally:
        conn.rollback()
        conn.execute(f"DROP SCHEMA IF EXISTS {ESQUEMA} CASCADE")
        conn.commit()
        conn.close()


def _fila(filas: list[Fila], prefijo: str) -> Fila:
    return next(f for f in filas if f.nombre.startswith(prefijo))


@pytest.mark.parametrize("coleccion", list(COLECCIONES))
def test_float4_conserva_el_recall_que_exige_la_busqueda_de_diseno(
    resultados: dict, coleccion: str
) -> None:
    base = _fila(resultados[coleccion], "vector (float4)")
    assert base.recall >= UMBRAL_RECALL_FLOAT4, (
        f"{coleccion}: el recall@10 de vector(1024) con ef_search={EF_SEARCH} es {base.recall:.3f}, "
        f"inferior a {UMBRAL_RECALL_FLOAT4} (el umbral de Diseño en vec.parametros_busqueda es 0,90 con margen)"
    )


@pytest.mark.parametrize("coleccion", list(COLECCIONES))
def test_el_redondeo_a_float2_por_si_solo_pierde_menos_de_un_punto(
    resultados: dict, coleccion: str
) -> None:
    exacto = _fila(resultados[coleccion], "halfvec exacto")
    assert 1 - exacto.recall < UMBRAL_PERDIDA, (coleccion, exacto.recall)


@pytest.mark.parametrize("coleccion", list(COLECCIONES))
def test_la_binaria_con_reordenacion_x200_iguala_al_float4(
    resultados: dict, coleccion: str
) -> None:
    base = _fila(resultados[coleccion], "vector (float4)")
    binaria = _fila(resultados[coleccion], "binaria + reordenación exacta (x200)")
    assert base.recall - binaria.recall < UMBRAL_PERDIDA, (coleccion, base.recall, binaria.recall)
    assert binaria.indice < 0.25 * base.indice, "el índice binario debe ser mucho menor"
    assert binaria.tabla > 0.9 * base.tabla, (
        "se guarda el vector completo para reordenar: la tabla no baja"
    )


@pytest.mark.parametrize("coleccion", list(COLECCIONES))
def test_halfvec_reduce_a_la_mitad_la_tabla_y_un_tercio_el_indice(
    resultados: dict, coleccion: str
) -> None:
    base, half = (
        _fila(resultados[coleccion], "vector (float4)"),
        _fila(resultados[coleccion], "halfvec (float2)"),
    )
    assert half.tabla < 0.55 * base.tabla, (half.tabla, base.tabla)
    assert half.indice < 0.45 * base.indice, (half.indice, base.indice)


@pytest.mark.parametrize("coleccion", list(COLECCIONES))
def test_aviso_si_halfvec_pasa_el_criterio_de_adopcion(resultados: dict, coleccion: str) -> None:
    """No falla nunca: avisa (REABRIR ADR-022) si halfvec con HNSW ya pierde menos de un punto."""
    perdida = _perdida_halfvec(resultados[coleccion])
    if perdida < UMBRAL_PERDIDA:
        warnings.warn(
            f"REABRIR ADR-022: {coleccion}: halfvec pierde {100 * perdida:.1f} puntos (< 1)",
            stacklevel=1,
        )


def _perdida_halfvec(filas: list[Fila]) -> float:
    return _fila(filas, "vector (float4)").recall - _fila(filas, "halfvec (float2)").recall


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tabla", action="store_true")
    ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    c = _conectar()
    try:
        for nombre, categoria in COLECCIONES.items():
            print(f"\n### {nombre} ({categoria}, {N} filas)\n")
            filas = evaluar_coleccion(c, nombre)
            print(tabla_markdown(filas, N), flush=True)
            perdida = _perdida_halfvec(filas)
            veredicto = (
                "REABRIR ADR-022 (< 1 punto: cumple el criterio de adopción)"
                if perdida < UMBRAL_PERDIDA
                else "no cumple el criterio de adopción (< 1 punto)"
            )
            print(
                f"\nPérdida de recall@10 de halfvec con HNSW: {100 * perdida:.1f} puntos -> {veredicto}",
                flush=True,
            )
    finally:
        c.rollback()
        c.execute(f"DROP SCHEMA IF EXISTS {ESQUEMA} CASCADE")
        c.commit()
        c.close()
