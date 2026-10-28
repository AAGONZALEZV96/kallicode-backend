"""Benchmark de recall de la búsqueda vectorial filtrada por tenant (KC-DB-08-1, DB-F10).

Un índice HNSW indexa TODOS los tenants y la política RLS filtra por tenant DESPUÉS de recorrer el
índice: con `hnsw.ef_search = 40` y un tenant que es el 1 % del corpus, de 40 candidatos solo ~0,4 son
suyos. pgvector 0.8 lo resuelve con `hnsw.iterative_scan` (seguir buscando hasta reunir los resultados)
acotado por `hnsw.max_scan_tuples`. Este benchmark mide el recall con el índice real (HNSW m=16,
ef_construction=64, distancia coseno, 1024 dimensiones) y la política real de tenant, para tenants que
son el 1 %, el 5 % y el 50 % del corpus, y comprueba que los parámetros de `vec.parametros_busqueda`
(los que usa el backend mediante `vec.configurar_busqueda`) cumplen el umbral de cada caso de uso.

No es un test unitario: tarda unos minutos (construye el índice) y por eso el archivo no se llama
`test_*`: no lo recoge `pytest` por defecto. Se ejecuta a propósito (CI nocturno):

    uv run pytest packages/core/tests/bench_recall_vectorial.py -v          # umbral: falla si el recall baja
    uv run python packages/core/tests/bench_recall_vectorial.py --calibrar  # imprime la tabla de calibración

Requiere KC_DATABASE_URL_MIGRATIONS (superusuario, crea el corpus en un esquema temporal) y
KC_DATABASE_URL (kallicode_app, con la configuración por rol real), con las migraciones aplicadas.

Diseño del corpus (determinista, semilla fija). En cada «evento» hay una consulta q y los vectores se
construyen con un coseno EXACTO respecto de q:
  * el tenant de prueba tiene 12 vecinos de q con coseno 0,95, 0,93, ..., 0,73 (todos por encima del
    umbral de duplicado 0,70, con huecos de 0,02 entre ellos: el top-10 exacto está bien definido);
  * los demás tenants tienen n_otros = 12·(1−s)/s vectores con coseno aleatorio en [0,72, 0,99], de modo
    que, entre los vecinos de q, la fracción que es del tenant es su cuota s del corpus (el caso difícil
    del filtrado: la mayor parte de los candidatos cercanos se descartan por RLS);
  * el resto del corpus hasta cuadrar las cuotas son vectores aleatorios (coseno ~0 con cualquier q).
"""

from __future__ import annotations

import argparse
import os
import struct
import sys
from dataclasses import dataclass

import numpy as np
import psycopg
import pytest

pytestmark = pytest.mark.benchmark

DIM = 1024
N_CORPUS = 40_000
SEMILLA = 20261024
UMBRAL_DUPLICADO = 0.70
K = 10
ESQUEMA = "bench_vec"
ROL_APP = "kallicode_app"
N_OTROS_TENANTS = 22
# Vecinos de otros tenants por consulta, respecto de los proporcionales a la cuota (12·(1−s)/s).
# 0,05 es la densidad de REFERENCIA: un tenant del 1 % tiene ~60 vecinos ajenos con coseno >= 0,72 por
# consulta (~70 vecinos en total alrededor de cada consulta, el 0,2 % del corpus). Con densidades
# mayores el tenant del 1 % deja de alcanzar el umbral solo con estos parámetros (docs/plan-indices.md,
# «Sensibilidad»); 1,0 es el caso extremo (~1.200 vecinos ajenos, el 3 % del corpus).
DENSIDAD_AJENOS = float(os.getenv("KC_BENCH_DENSIDAD", "0.05"))

# (nombre, cuota del corpus, eventos). Todos con 12 vecinos propios por evento.
ESCENARIOS = {
    "1%": ("bench_t01", 0.01, 10),
    "5%": ("bench_t05", 0.05, 10),
    "50%": ("bench_t50", 0.50, 10),
}
# Umbral de recall por caso de uso: lo que el ticket exige para la deduplicación y lo que se pide a Diseño.
UMBRAL_RECALL = {"dedup": 0.98, "diseno": 0.90}


def _dsn(variable: str) -> str:
    url = os.getenv(variable)
    if not url:
        pytest.skip(f"{variable} no definida")  # en CI nocturno siempre está definida
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


# ---------------------------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------------------------
@dataclass
class Evento:
    escenario: str
    tenant: str
    consulta: np.ndarray  # vector unitario
    relevantes: list[int]  # ids de los 12 vecinos del tenant, de mayor a menor coseno


def _unitario(rng: np.random.Generator, n: int) -> np.ndarray:
    v = rng.standard_normal((n, DIM)).astype(np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    return v


def _con_coseno(rng: np.random.Generator, q: np.ndarray, cosenos: np.ndarray) -> np.ndarray:
    """Vectores unitarios con coseno EXACTO `cosenos[i]` respecto de q."""
    w = rng.standard_normal((len(cosenos), DIM)).astype(np.float64)
    w -= (w @ q.astype(np.float64))[:, None] * q.astype(np.float64)[None, :]  # ortogonal a q
    w /= np.linalg.norm(w, axis=1, keepdims=True)
    a = cosenos.astype(np.float64)[:, None]
    return (a * q[None, :] + np.sqrt(1.0 - a * a) * w).astype(np.float32)


def _binario(v: np.ndarray) -> bytes:
    """Un vector en el formato binario de pgvector (dim int16, 0 int16, float32 big-endian)."""
    return struct.pack(">HH", DIM, 0) + v.astype(">f4").tobytes()


def _texto(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.7g}" for x in v) + "]"


def construir_corpus(admin: psycopg.Connection, *, reusar: bool = False) -> list[Evento]:
    """Genera el corpus y lo carga. Con reusar=True y el esquema ya cargado (exploración manual) solo
    regenera los vectores en memoria (misma semilla, mismos ids) y no vuelve a construir el índice."""
    rng = np.random.default_rng(SEMILLA)
    ya = (
        reusar
        and admin.execute("SELECT to_regclass(%s)", (f"{ESQUEMA}.emb",)).fetchone()[0] is not None
    )
    if not ya:
        admin.execute(f"DROP SCHEMA IF EXISTS {ESQUEMA} CASCADE")
        admin.execute(f"CREATE SCHEMA {ESQUEMA}")
        admin.execute(
            f"""
            CREATE TABLE {ESQUEMA}.emb (
                id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                tenant_id text NOT NULL,
                evento    integer,
                embedding vector({DIM}) NOT NULL
            )"""
        )
    otros = [f"bench_otro_{i:02d}" for i in range(N_OTROS_TENANTS)]
    cuota_otros = (
        N_CORPUS - sum(round(c * N_CORPUS) for _, c, _ in ESCENARIOS.values())
    ) // N_OTROS_TENANTS

    filas: list[tuple[str, int | None, np.ndarray]] = []
    eventos: list[Evento] = []
    cuenta: dict[str, int] = {}
    contador_evento = 0

    def anadir(tenant: str, evento: int | None, vecs: np.ndarray) -> list[int]:
        ini = len(filas)
        for v in vecs:
            filas.append((tenant, evento, v))
        cuenta[tenant] = cuenta.get(tenant, 0) + len(vecs)
        return list(range(ini, ini + len(vecs)))

    # 1) Eventos: vecinos exactos de cada consulta.
    pos_relevantes: list[tuple[Evento, list[int]]] = []
    for nombre, (tenant, cuota, n_eventos) in ESCENARIOS.items():
        n_otros_evento = max(round(12 * (1 - cuota) / cuota * DENSIDAD_AJENOS), 1)
        for _ in range(n_eventos):
            contador_evento += 1
            q = _unitario(rng, 1)[0]
            propios = anadir(
                tenant, contador_evento, _con_coseno(rng, q, 0.95 - 0.02 * np.arange(12))
            )
            ajenos = _con_coseno(rng, q, rng.uniform(0.72, 0.99, n_otros_evento))
            for i in range(len(ajenos)):
                anadir(otros[i % N_OTROS_TENANTS], contador_evento, ajenos[i : i + 1])
            ev = Evento(nombre, tenant, q, [])
            eventos.append(ev)
            pos_relevantes.append((ev, propios))

    # 2) Relleno aleatorio hasta la cuota de cada tenant.
    for nombre, (tenant, cuota, _) in ESCENARIOS.items():
        falta = round(cuota * N_CORPUS) - cuenta.get(tenant, 0)
        anadir(tenant, None, _unitario(rng, max(falta, 0)))
    for t in otros:
        falta = cuota_otros - cuenta.get(t, 0)
        if falta > 0:
            anadir(t, None, _unitario(rng, falta))

    # 3) Carga binaria. Los ids son 1..N en el orden de inserción.
    if not ya:
        with (
            admin.cursor() as cur,
            cur.copy(
                f"COPY {ESQUEMA}.emb (tenant_id, evento, embedding) FROM STDIN (FORMAT BINARY)"
            ) as cp,
        ):
            cp.set_types(["text", "int4", "bytea"])
            for tenant, evento, v in filas:
                cp.write_row((tenant, evento, _binario(v)))
    for ev, propios in pos_relevantes:
        ev.relevantes = [p + 1 for p in propios]

    # 4) Índice igual al de producción (db/02_indexes.sql) y política igual a la de tenant_isolation.
    if not ya:
        admin.execute("SET maintenance_work_mem = '1GB'")
        admin.execute(
            f"CREATE INDEX emb_hnsw_ix ON {ESQUEMA}.emb USING hnsw (embedding vector_cosine_ops) "
            "WITH (m = 16, ef_construction = 64)"
        )
        admin.execute(f"ALTER TABLE {ESQUEMA}.emb ENABLE ROW LEVEL SECURITY")
        admin.execute(f"ALTER TABLE {ESQUEMA}.emb FORCE ROW LEVEL SECURITY")
        admin.execute(
            f"CREATE POLICY tenant_isolation ON {ESQUEMA}.emb USING (tenant_id = core.current_tenant())"
        )
        admin.execute(f"GRANT USAGE ON SCHEMA {ESQUEMA} TO {ROL_APP}")
        admin.execute(f"GRANT SELECT ON {ESQUEMA}.emb TO {ROL_APP}")
        admin.execute(f"ANALYZE {ESQUEMA}.emb")
    admin.commit()
    return eventos


# ---------------------------------------------------------------------------------------------
# Medición
# ---------------------------------------------------------------------------------------------
def _consultar(
    app: psycopg.Connection, ev: Evento, *, ajustes: dict[str, str] | None, caso: str | None
) -> list[tuple[int, float]]:
    """La consulta de producción: SET LOCAL en la transacción y búsqueda por coseno con LIMIT 10."""
    with app.transaction():
        app.execute("SELECT set_config('app.tenant_id', %s, true)", (ev.tenant,))
        # Con un corpus de 40.000 filas el planificador prefiere recorrer la tabla y ordenar (no es
        # el caso con millones de filas, que es el que se quiere medir): se le obliga a usar el índice.
        app.execute("SET LOCAL enable_seqscan = off")
        if caso is not None:
            app.execute("SELECT vec.configurar_busqueda(%s)", (caso,))
        for clave, valor in (ajustes or {}).items():
            app.execute("SELECT set_config(%s, %s, true)", (clave, valor))
        filas = app.execute(
            f"SELECT id, 1 - (embedding <=> %s::vector) FROM {ESQUEMA}.emb "
            "WHERE tenant_id = %s ORDER BY embedding <=> %s::vector LIMIT %s",
            (_texto(ev.consulta), ev.tenant, _texto(ev.consulta), K),
        ).fetchall()
    return [(int(i), float(s)) for i, s in filas]


@dataclass
class Resultado:
    recall_at_10: float  # |devueltos ∩ top-10 exacto| / 10
    recall_duplicados: float  # duplicados (coseno >= 0,70) devueltos, con tope 10, / 10
    filas_por_consulta: float
    ms_p50: float
    ms_max: float


def medir(
    app: psycopg.Connection, eventos: list[Evento], escenario: str, *, ajustes=None, caso=None
) -> Resultado:
    import time

    propios = [e for e in eventos if e.escenario == escenario]
    aciertos = dups = n = 0
    tiempos: list[float] = []
    filas_total = 0
    for ev in propios:
        t0 = time.perf_counter()
        devueltos = _consultar(app, ev, ajustes=ajustes, caso=caso)
        tiempos.append((time.perf_counter() - t0) * 1000)
        ids = {i for i, _ in devueltos}
        aciertos += len(ids & set(ev.relevantes[:K]))
        dups += min(sum(1 for i, s in devueltos if s >= UMBRAL_DUPLICADO), K)
        filas_total += len(devueltos)
        n += 1
    tiempos.sort()
    return Resultado(
        recall_at_10=aciertos / (n * K),
        recall_duplicados=dups / (n * K),
        filas_por_consulta=filas_total / n,
        ms_p50=tiempos[len(tiempos) // 2],
        ms_max=tiempos[-1],
    )


def verificar_corpus(app: psycopg.Connection, eventos: list[Evento]) -> None:
    """El top-10 exacto de cada consulta (sin índice) es el que dice la construcción del corpus, y la
    consulta medida recorre de verdad el índice HNSW."""
    ev0 = eventos[0]
    with app.transaction():
        app.execute("SELECT set_config('app.tenant_id', %s, true)", (ev0.tenant,))
        app.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(
            r[0]
            for r in app.execute(
                f"EXPLAIN SELECT id FROM {ESQUEMA}.emb WHERE tenant_id = %s "
                "ORDER BY embedding <=> %s::vector LIMIT 10",
                (ev0.tenant, _texto(ev0.consulta)),
            ).fetchall()
        )
    assert "emb_hnsw_ix" in plan, f"la consulta medida no usa el índice HNSW:\n{plan}"
    for ev in eventos:
        with app.transaction():
            app.execute("SELECT set_config('app.tenant_id', %s, true)", (ev.tenant,))
            app.execute("SET LOCAL enable_indexscan = off")
            app.execute("SET LOCAL enable_bitmapscan = off")
            exactos = [
                int(r[0])
                for r in app.execute(
                    f"SELECT id FROM {ESQUEMA}.emb WHERE tenant_id = %s "
                    "ORDER BY embedding <=> %s::vector LIMIT %s",
                    (ev.tenant, _texto(ev.consulta), K),
                ).fetchall()
            ]
        assert exactos == ev.relevantes[:K], f"el corpus no cumple su construcción ({ev.escenario})"


# ---------------------------------------------------------------------------------------------
# Calibración y umbrales
# ---------------------------------------------------------------------------------------------
def calibrar(app: psycopg.Connection, eventos: list[Evento]) -> list[dict]:
    """Rejilla de ef_search x max_scan_tuples para cada escenario (con iterative_scan = relaxed_order,
    como lo deja el rol) más las referencias sin escaneo iterativo."""
    filas = []
    configuraciones = [
        ("sin iterative_scan", {"hnsw.iterative_scan": "off"}, ef, 20000) for ef in (40, 100, 400)
    ]
    for ef in (40, 100, 200, 300, 400, 600):
        for mst in (1000, 5000, 20000):
            configuraciones.append(
                ("relaxed_order", {"hnsw.iterative_scan": "relaxed_order"}, ef, mst)
            )
    for etiqueta, base, ef, mst in configuraciones:
        for esc in ESCENARIOS:
            ajustes = {**base, "hnsw.ef_search": str(ef), "hnsw.max_scan_tuples": str(mst)}
            r = medir(app, eventos, esc, ajustes=ajustes)
            filas.append(
                {
                    "modo": etiqueta,
                    "ef_search": ef,
                    "max_scan_tuples": mst,
                    "escenario": esc,
                    "r": r,
                }
            )
    return filas


def _tabla_markdown(filas: list[dict]) -> str:
    out = [
        "| iterative_scan | ef_search | max_scan_tuples | tenant | recall@10 | recall duplicados ≥0,70 | filas/consulta | p50 ms | máx ms |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for f in filas:
        r: Resultado = f["r"]
        out.append(
            f"| {f['modo']} | {f['ef_search']} | {f['max_scan_tuples']} | {f['escenario']} | {r.recall_at_10:.3f} | "
            f"{r.recall_duplicados:.3f} | {r.filas_por_consulta:.1f} | {r.ms_p50:.1f} | {r.ms_max:.1f} |"
        )
    return "\n".join(out)


# ---------------------------------------------------------------------------------------------
# Pytest (CI nocturno)
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def corpus():
    admin = psycopg.connect(_dsn("KC_DATABASE_URL_MIGRATIONS"), autocommit=False)
    app = psycopg.connect(_dsn("KC_DATABASE_URL"), autocommit=True)
    try:
        eventos = construir_corpus(admin)
        verificar_corpus(app, eventos)
        yield app, eventos
    finally:
        app.close()
        admin.rollback()
        admin.execute(f"DROP SCHEMA IF EXISTS {ESQUEMA} CASCADE")
        admin.commit()
        admin.close()


def test_el_rol_de_la_aplicacion_trae_iterative_scan_relaxed_order(corpus) -> None:
    app, _ = corpus
    app.execute("SELECT 1")
    assert app.execute("SHOW hnsw.iterative_scan").fetchone()[0] == "relaxed_order", (
        "falta ALTER ROLE kallicode_app SET hnsw.iterative_scan = 'relaxed_order' (db/00_roles.sql)"
    )


@pytest.mark.parametrize("escenario", list(ESCENARIOS))
def test_la_deduplicacion_recupera_los_duplicados_con_recall_minimo(corpus, escenario: str) -> None:
    app, eventos = corpus
    r = medir(app, eventos, escenario, caso="dedup")
    assert r.recall_duplicados >= UMBRAL_RECALL["dedup"], (
        f"dedup, tenant del {escenario}: recall de duplicados {r.recall_duplicados:.3f} "
        f"< {UMBRAL_RECALL['dedup']} (recall@10 {r.recall_at_10:.3f}, p50 {r.ms_p50:.0f} ms)"
    )


@pytest.mark.parametrize("escenario", list(ESCENARIOS))
def test_la_recuperacion_de_diseno_cumple_su_recall_minimo(corpus, escenario: str) -> None:
    app, eventos = corpus
    r = medir(app, eventos, escenario, caso="diseno")
    assert r.recall_at_10 >= UMBRAL_RECALL["diseno"], (
        f"diseno, tenant del {escenario}: recall@10 {r.recall_at_10:.3f} < {UMBRAL_RECALL['diseno']} "
        f"(p50 {r.ms_p50:.0f} ms)"
    )


def test_sin_iterative_scan_el_recall_de_un_tenant_pequeno_es_inaceptable(corpus) -> None:
    """Prueba de que el benchmark mide algo: con los valores por defecto de antes (ef_search 40, sin
    escaneo iterativo) el tenant del 1 % no llega al umbral. Si esto empezara a pasar sin la mejora, el
    corpus habría dejado de ser un caso difícil."""
    app, eventos = corpus
    r = medir(app, eventos, "1%", ajustes={"hnsw.iterative_scan": "off", "hnsw.ef_search": "40"})
    assert r.recall_duplicados < UMBRAL_RECALL["dedup"]


# ---------------------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Calibración del recall vectorial (KC-DB-08-1)")
    parser.add_argument(
        "--calibrar", action="store_true", help="imprime la tabla de calibración en Markdown"
    )
    args = parser.parse_args()
    if not args.calibrar:
        parser.error("use --calibrar, o ejecute el archivo con pytest")
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # la consola de Windows usa cp1252
    admin_ = psycopg.connect(_dsn("KC_DATABASE_URL_MIGRATIONS"), autocommit=False)
    app_ = psycopg.connect(_dsn("KC_DATABASE_URL"), autocommit=True)
    try:
        ev_ = construir_corpus(admin_)
        verificar_corpus(app_, ev_)
        print(_tabla_markdown(calibrar(app_, ev_)))
    finally:
        app_.close()
        admin_.rollback()
        admin_.execute(f"DROP SCHEMA IF EXISTS {ESQUEMA} CASCADE")
        admin_.commit()
        admin_.close()
    sys.exit(0)
