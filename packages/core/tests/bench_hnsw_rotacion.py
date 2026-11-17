"""Benchmark de rotación diaria de los HNSW: recall@10, hinchazón y coste del mantenimiento (KC-CM-11-4).

El refresco diario de CodeMapping reescribe los embeddings de lo que cambió (UPSERT de los símbolos de
los ficheros modificados, borrado de los eliminados). En PostgreSQL cada UPDATE deja la versión vieja como
tupla muerta, y en un HNSW eso es peor que en un B-tree: el grafo conserva el nodo muerto hasta que un
VACUUM lo desconecta y repara a sus vecinos, y nunca se compacta hasta un REINDEX. El índice crece y el
recall baja porque la búsqueda gasta su presupuesto de candidatos (ef_search) en nodos que ya no existen.

Qué mide, sobre el corpus semi-real de BGE-M3 (packages/core/tests/data/corpus_bge_m3.npz, categoría
«definiciones», ampliado a N filas como en bench_halfvec.py; ADR-022 explica sus límites):

  * por configuración del índice (m, ef_construction, representación): recall@10 frente al top-10 EXACTO
    del día 0 y tras D días de rotación con un R % de reembebido diario;
  * dos estrategias de mantenimiento: sin VACUUM (autovacuum apagado: el peor caso, lo que pasa si la
    rutina no llega a correr) y con el VACUUM (ANALYZE) diario de la rutina de la migración 060;
  * el tamaño y la hinchazón del índice (bytes por fila viva frente al día 0, el mismo indicador que
    vec.medir_hnsw) y la duración del VACUUM diario y del REINDEX semanal: la ventana y el coste de la
    rutina programada con pg_cron.

No es un test unitario (construye varios índices: unos minutos) y por eso el archivo no se llama test_*.
Lo ejecuta el CI nocturno (.github/workflows/recall_vectorial_nocturno.yml):

    uv run pytest packages/core/tests/bench_hnsw_rotacion.py -v           # criterios (falla si no se cumplen)
    uv run python packages/core/tests/bench_hnsw_rotacion.py               # imprime la tabla
    uv run python packages/core/tests/bench_hnsw_rotacion.py --json docs/benchmarks/hnsw_rotacion_v1.json

Requiere KC_DATABASE_URL_MIGRATIONS (crea y borra el esquema bench_rot). Variables: KC_BENCH_ROT_N (filas,
20000), KC_BENCH_ROT_DIAS (7), KC_BENCH_ROT_PCT (rotación diaria, 0.05), KC_BENCH_ROT_EF (ef_search, 100).
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import psycopg
import pytest

pytestmark = pytest.mark.benchmark

DIM = 1024
N = int(os.getenv("KC_BENCH_ROT_N", "20000"))
DIAS = int(os.getenv("KC_BENCH_ROT_DIAS", "7"))
ROTACION = float(os.getenv("KC_BENCH_ROT_PCT", "0.05"))
# ef_search de la medición: bajo a propósito (el de Diseño, 400, satura el recall y oculta la degradación).
EF_SEARCH = int(os.getenv("KC_BENCH_ROT_EF", "100"))
N_CONSULTAS = 200
K = 10
SEMILLA = 20261115
ESQUEMA = "bench_rot"
DATOS = Path(__file__).parent / "data" / "corpus_bge_m3.npz"
# Pérdida de recall@10 tolerada: 1 punto, el criterio de adopción de ADR-022 (el ruido de 200 consultas y
# de la construcción paralela del grafo mueve el recall entre 0,3 y 0,5 puntos de una corrida a otra).
TOLERANCIA = 0.01
# Categoría de semillas del corpus real: definiciones (vec.definition_embeddings) o docs (vec.doc_chunks).
CATEGORIA = os.getenv("KC_BENCH_ROT_CATEGORIA", "definiciones")

# (nombre, tipo, m, ef_construction).
CONFIGURACIONES_TODAS: tuple[tuple[str, str, int, int], ...] = (
    ("vector m16 ef64", "vector", 16, 64),
    ("vector m16 ef128", "vector", 16, 128),
    ("vector m24 ef128", "vector", 24, 128),
    ("halfvec m16 ef64", "halfvec", 16, 64),
)
# La anterior a KC-CM-11-4 (db/02_indexes.sql) y la elegida (vec.hnsw_catalogo desde la migración 060).
ANTERIOR = "vector m16 ef64"
ELEGIDA = "vector m24 ef128"
# KC_BENCH_ROT_CONFIGS="vector m16 ef64,vector m24 ef128" mide solo esas (la anterior y la elegida siempre).
_pedidas = {c.strip() for c in os.getenv("KC_BENCH_ROT_CONFIGS", "").split(",") if c.strip()}
CONFIGURACIONES = tuple(
    c for c in CONFIGURACIONES_TODAS if not _pedidas or c[0] in _pedidas | {ANTERIOR, ELEGIDA}
)
ESTRATEGIAS = ("sin_vacuum", "vacuum_diario")


@dataclass
class Medicion:
    configuracion: str
    estrategia: str
    recall_dia0: float
    recall_final: float
    recall_minimo: float
    indice_dia0_bytes: int
    indice_final_bytes: int
    hinchazon_pct: float
    vacuum_s_por_dia: float | None
    reindex_s: float
    indice_tras_reindex_bytes: int
    recall_tras_reindex: float
    recall_por_dia: list[float] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------
def _dsn() -> str:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def cargar_semillas() -> np.ndarray:
    if not DATOS.exists():
        pytest.skip(f"falta el corpus real {DATOS}")
    m = np.load(DATOS)[CATEGORIA].astype(np.float32)
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def ampliar(semillas: np.ndarray, n: int, rng: np.random.Generator) -> np.ndarray:
    """Mezcla pares de vectores reales y añade ruido pequeño (misma receta que bench_halfvec.py)."""
    s = len(semillas)
    i, j = rng.integers(0, s, n), rng.integers(0, s, n)
    a = rng.beta(0.6, 0.6, (n, 1)).astype(np.float32)
    v = (
        a * semillas[i]
        + (1 - a) * semillas[j]
        + 0.03 * rng.standard_normal((n, DIM)).astype(np.float32) / np.sqrt(DIM)
    )
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def _binario(v: np.ndarray, tipo: str) -> bytes:
    formato = ">f2" if tipo == "halfvec" else ">f4"
    return struct.pack(">HH", DIM, 0) + v.astype(formato).tobytes()


def top_exacto(corpus: np.ndarray, vivos: np.ndarray, consultas: np.ndarray) -> list[set[int]]:
    """Top-K exacto por coseno (los vectores están normalizados) entre las filas vivas."""
    sims = consultas @ corpus[vivos].T
    idx = np.argpartition(-sims, K, axis=1)[:, :K]
    return [{int(vivos[j]) for j in fila} for fila in idx]


# ---------------------------------------------------------------------------
# Base de datos
# ---------------------------------------------------------------------------
def _tabla(tipo: str, m: int, ef: int) -> str:
    return f"{ESQUEMA}.t_{tipo}_m{m}_ef{ef}"


def _indice_bytes(conn: psycopg.Connection, tabla: str) -> int:
    fila = conn.execute(f"SELECT pg_relation_size('{tabla}_hnsw'::regclass)").fetchone()
    return int(fila[0]) if fila else 0


def _copiar(
    conn: psycopg.Connection, tabla: str, tipo: str, ids: np.ndarray, vecs: np.ndarray
) -> None:
    with (
        conn.cursor() as cur,
        cur.copy(f"COPY {tabla} (id, embedding) FROM STDIN (FORMAT BINARY)") as cp,
    ):
        cp.set_types(["int4", "bytea"])
        for i, v in zip(ids, vecs, strict=True):
            cp.write_row((int(i), _binario(v, tipo)))


def construir(conn: psycopg.Connection, tipo: str, m: int, ef: int, corpus: np.ndarray) -> str:
    tabla = _tabla(tipo, m, ef)
    conn.execute(f"DROP TABLE IF EXISTS {tabla}")
    # Autovacuum apagado: la estrategia decide cuándo se limpia (si no, el resultado dependería de cuándo
    # pasa el lanzador de autovacuum durante la prueba).
    conn.execute(
        f"CREATE TABLE {tabla} (id int PRIMARY KEY, embedding {tipo}({DIM}) NOT NULL) "
        "WITH (autovacuum_enabled = false)"
    )
    _copiar(conn, tabla, tipo, np.arange(len(corpus)), corpus)
    opclase = "halfvec_cosine_ops" if tipo == "halfvec" else "vector_cosine_ops"
    conn.execute(
        f"CREATE INDEX {tabla.split('.')[1]}_hnsw ON {tabla} USING hnsw (embedding {opclase}) "
        f"WITH (m = {m}, ef_construction = {ef})"
    )
    conn.execute(f"ANALYZE {tabla}")
    return tabla


def recall(
    conn: psycopg.Connection, tabla: str, tipo: str, consultas: np.ndarray, verdad: list[set[int]]
) -> float:
    aciertos = 0
    with conn.transaction():
        conn.execute(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}")
        conn.execute("SET LOCAL hnsw.iterative_scan = off")
        conn.execute("SET LOCAL enable_seqscan = off")
        for q, esperado in zip(consultas, verdad, strict=True):
            filas = conn.execute(
                f"SELECT id FROM {tabla} ORDER BY embedding <=> %s::{tipo} LIMIT {K}",
                ("[" + ",".join(f"{x:.7g}" for x in q) + "]",),
            ).fetchall()
            aciertos += len({int(f[0]) for f in filas} & esperado)
    return aciertos / (K * len(consultas))


def rotar(
    conn: psycopg.Connection,
    tabla: str,
    tipo: str,
    corpus: np.ndarray,
    semillas: np.ndarray,
    rng: np.random.Generator,
) -> None:
    """Un día de refresco: el R % de las filas se reembebe (descripción nueva -> vector nuevo)."""
    n_rot = int(len(corpus) * ROTACION)
    ids = rng.choice(len(corpus), n_rot, replace=False)
    nuevos = ampliar(semillas, n_rot, rng)
    corpus[ids] = nuevos
    # Igual que GraphLoader._copy_upsert: COPY a una tabla de paso y UPSERT.
    conn.execute(f"CREATE TEMP TABLE _rot (id int, embedding {tipo}({DIM})) ON COMMIT DROP")
    _copiar(conn, "_rot", tipo, ids, nuevos)
    conn.execute(
        f"INSERT INTO {tabla} (id, embedding) SELECT id, embedding FROM _rot "
        "ON CONFLICT (id) DO UPDATE SET embedding = EXCLUDED.embedding"
    )


def evaluar(
    admin: psycopg.Connection,
    nombre: str,
    tipo: str,
    m: int,
    ef: int,
    estrategia: str,
    semillas: np.ndarray,
    corpus0: np.ndarray,
    consultas: np.ndarray,
) -> Medicion:
    rng = np.random.default_rng(SEMILLA + 1)  # misma rotación para todas las configuraciones
    corpus = corpus0.copy()
    vivos = np.arange(len(corpus))
    tabla = construir(admin, tipo, m, ef, corpus)
    tam0 = _indice_bytes(admin, tabla)
    r0 = recall(admin, tabla, tipo, consultas, top_exacto(corpus, vivos, consultas))
    por_dia: list[float] = []
    vacuum_s: list[float] = []
    for _ in range(DIAS):
        with admin.transaction():
            rotar(admin, tabla, tipo, corpus, semillas, rng)
        if estrategia == "vacuum_diario":
            t = time.perf_counter()
            admin.execute(f"VACUUM (ANALYZE) {tabla}")
            vacuum_s.append(time.perf_counter() - t)
        por_dia.append(recall(admin, tabla, tipo, consultas, top_exacto(corpus, vivos, consultas)))
    tam_final = _indice_bytes(admin, tabla)
    t = time.perf_counter()
    admin.execute(f"REINDEX INDEX {tabla}_hnsw")
    reindex_s = time.perf_counter() - t
    r_reindex = recall(admin, tabla, tipo, consultas, top_exacto(corpus, vivos, consultas))
    tam_reindex = _indice_bytes(admin, tabla)
    admin.execute(f"DROP TABLE {tabla}")
    return Medicion(
        configuracion=nombre,
        estrategia=estrategia,
        recall_dia0=round(r0, 4),
        recall_final=round(por_dia[-1], 4),
        recall_minimo=round(min(por_dia), 4),
        indice_dia0_bytes=tam0,
        indice_final_bytes=tam_final,
        hinchazon_pct=round(100 * (tam_final - tam0) / tam0, 1),
        vacuum_s_por_dia=round(float(np.mean(vacuum_s)), 2) if vacuum_s else None,
        reindex_s=round(reindex_s, 2),
        indice_tras_reindex_bytes=tam_reindex,
        recall_tras_reindex=round(r_reindex, 4),
        recall_por_dia=[round(x, 4) for x in por_dia],
    )


def ejecutar(
    configuraciones: tuple[tuple[str, str, int, int], ...] = CONFIGURACIONES,
) -> list[Medicion]:
    rng = np.random.default_rng(SEMILLA)
    semillas = cargar_semillas()
    corpus0 = ampliar(semillas, N, rng)
    consultas = ampliar(semillas, N_CONSULTAS, rng)  # mezclas nuevas: no están en el corpus
    medidas: list[Medicion] = []
    with psycopg.connect(_dsn(), autocommit=True) as admin:
        admin.execute(f"DROP SCHEMA IF EXISTS {ESQUEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {ESQUEMA}")
        admin.execute("SET maintenance_work_mem = '512MB'")
        try:
            for nombre, tipo, m, ef in configuraciones:
                # La degradación sin mantenimiento solo se mide en la anterior (es la referencia).
                estrategias = ESTRATEGIAS if nombre == ANTERIOR else ("vacuum_diario",)
                for estrategia in estrategias:
                    medidas.append(
                        evaluar(
                            admin, nombre, tipo, m, ef, estrategia, semillas, corpus0, consultas
                        )
                    )
        finally:
            admin.execute(f"DROP SCHEMA IF EXISTS {ESQUEMA} CASCADE")
    return medidas


def tabla_markdown(medidas: list[Medicion]) -> str:
    cab = (
        f"| Configuración | Mantenimiento | recall@10 día 0 | día {DIAS} | mínimo | índice día 0 | "
        f"día {DIAS} | hinchazón | VACUUM/día | REINDEX | recall tras REINDEX |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|\n"
    )
    filas = [
        f"| {m.configuracion} | {m.estrategia} | {m.recall_dia0:.3f} | {m.recall_final:.3f} | "
        f"{m.recall_minimo:.3f} | {m.indice_dia0_bytes / 2**20:.1f} MB | "
        f"{m.indice_final_bytes / 2**20:.1f} MB | {m.hinchazon_pct:+.1f} % | "
        f"{'-' if m.vacuum_s_por_dia is None else f'{m.vacuum_s_por_dia:.1f} s'} | "
        f"{m.reindex_s:.1f} s | {m.recall_tras_reindex:.3f} |"
        for m in medidas
    ]
    return cab + "\n".join(filas)


# ---------------------------------------------------------------------------
# Criterios (pytest)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def medidas() -> dict[tuple[str, str], Medicion]:
    return {(m.configuracion, m.estrategia): m for m in ejecutar()}


def test_sin_mantenimiento_la_rotacion_hincha_el_indice(medidas) -> None:
    sin = medidas[(ANTERIOR, "sin_vacuum")]
    con = medidas[(ANTERIOR, "vacuum_diario")]
    assert sin.hinchazon_pct > 2 * con.hinchazon_pct


def test_con_la_elegida_el_vacuum_diario_conserva_el_recall_del_dia_0(medidas) -> None:
    con = medidas[(ELEGIDA, "vacuum_diario")]
    assert con.recall_minimo >= con.recall_dia0 - TOLERANCIA, con.recall_por_dia


def test_cambiar_a_la_elegida_no_baja_el_recall(medidas) -> None:
    """Criterio de KC-CM-11-4: recall@10 antes (anterior) y después (elegida), al construir y bajo
    rotación con la rutina diaria."""
    antes = medidas[(ANTERIOR, "vacuum_diario")]
    despues = medidas[(ELEGIDA, "vacuum_diario")]
    assert despues.recall_dia0 >= antes.recall_dia0 - TOLERANCIA
    assert despues.recall_minimo >= antes.recall_minimo
    assert despues.recall_tras_reindex >= antes.recall_tras_reindex - TOLERANCIA


def test_la_elegida_no_ocupa_mas_que_la_anterior(medidas) -> None:
    """Con 1024 dimensiones en float4 cada elemento ocupa una página: m no cambia el tamaño."""
    antes = medidas[(ANTERIOR, "vacuum_diario")]
    despues = medidas[(ELEGIDA, "vacuum_diario")]
    assert despues.indice_dia0_bytes <= antes.indice_dia0_bytes * 1.05


def test_el_reindex_semanal_devuelve_el_indice_a_su_tamano(medidas) -> None:
    sin = medidas[(ANTERIOR, "sin_vacuum")]
    assert sin.indice_tras_reindex_bytes <= sin.indice_dia0_bytes * 1.05


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--json", type=Path, help="escribe las mediciones en este JSON")
    args = ap.parse_args()
    t = time.perf_counter()
    medidas_ = ejecutar()
    print(
        f"N = {N} filas, {DIAS} días, rotación {ROTACION:.0%}/día, ef_search = {EF_SEARCH}, "
        f"{N_CONSULTAS} consultas ({time.perf_counter() - t:.0f} s)\n"
    )
    print(tabla_markdown(medidas_))
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "n": N,
                    "dias": DIAS,
                    "rotacion": ROTACION,
                    "ef_search": EF_SEARCH,
                    "consultas": N_CONSULTAS,
                    "mediciones": [asdict(m) for m in medidas_],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
