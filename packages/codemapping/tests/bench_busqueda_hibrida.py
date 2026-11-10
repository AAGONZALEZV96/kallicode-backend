"""Benchmark de la búsqueda híbrida con 200.000 vectores (KC-CM-08-4).

Carga 200.000 vectores de 1024 dimensiones en las tablas REALES (180.000 en
vec.definition_embeddings, con sus nodos en cm.definitions/cm.concepts, y 20.000 en vec.doc_chunks,
con enlaces al grafo y nuggets), repartidos entre diez organizaciones, y mide cm.buscar_hibrido por
la ruta de producción (kallicode_codemapping.cm_queries.buscar_hibrido como kallicode_app, con el
índice HNSW real, RLS y los parámetros de 'diseno').

Umbrales (falla si no se cumplen):
- latencia p95 < 400 ms por tenant (criterio del ticket; el objetivo de diseño de 80 ms se informa);
- recall@10 >= 0,90 frente al top-10 exacto, con filtro de capa, para el tenant grande y el pequeño;
- el filtro por tenant aparece en el plan real (EXPLAIN ANALYZE) de cada recorrido vectorial.

No es un test unitario: tarda varios minutos (inserta 200.000 vectores en índices HNSW) y por eso el
archivo no se llama `test_*`. Se ejecuta a propósito (CI semanal, busqueda_hibrida_semanal.yml):

    uv run pytest packages/codemapping/tests/bench_busqueda_hibrida.py -v -s
    uv run python packages/codemapping/tests/bench_busqueda_hibrida.py   # imprime el informe

Requiere KC_DATABASE_URL_MIGRATIONS (superusuario: siembra) con las migraciones aplicadas. Corpus:
100 grupos semánticos (centroide + ruido, semilla fija); cada consulta parte del centroide de un
grupo, así que cada una tiene vecinos reales en cada tenant.
"""

from __future__ import annotations

import os
import statistics
import struct
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
import pytest
from kallicode_codemapping.cm_queries import buscar_hibrido
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_core import cm_busqueda
from kallicode_core.cm_busqueda import BusquedaHibridaIn, CapaBusqueda

np = pytest.importorskip("numpy")

pytestmark = pytest.mark.benchmark

DIM = 1024
N_DEFINICIONES = 180_000
N_CHUNKS = 20_000
# 100 grupos: el tenant del 1 % tiene ~13 definiciones por grupo, así que el top-10 exacto es de
# vecinos reales y no de vectores casi ortogonales empatados.
N_GRUPOS = 100
N_MODULOS = 50
RUIDO = 0.9  # norma relativa del ruido respecto del centroide: vecindarios densos pero no triviales
SEMILLA = 20261109
SNAP = "snap_bench_bus"
# (tenant, cuota del corpus). bus_grande y bus_pequeno son los que se miden.
TENANTS = (("bench_bus_grande", 0.10), ("bench_bus_pequeno", 0.01)) + tuple(
    (f"bench_bus_otro_{i}", 0.89 / 8) for i in range(8)
)
MEDIDOS = ("bench_bus_grande", "bench_bus_pequeno")
N_CONSULTAS = 200
N_CONSULTAS_RECALL = 30
UMBRAL_P95_MS = 400.0
OBJETIVO_P95_MS = 80.0
UMBRAL_RECALL = 0.90


def _dsn_admin() -> str:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")  # en CI siempre está
    return normalizar_dsn(url)


def _dsn_app(admin: str) -> str:
    return admin + ("&" if "?" in admin else "?") + "options=-c%20role%3Dkallicode_app"


# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------
@dataclass
class Corpus:
    centroides: Any  # (N_GRUPOS, DIM) float32 unitarios
    # Por tenant: ids de vec.definition_embeddings, capa y vectores (para el top-k exacto).
    ids: dict[str, list[str]]
    capas: dict[str, Any]
    vectores: dict[str, Any]


def _unitarios(m: Any) -> Any:
    return (m / np.linalg.norm(m, axis=1, keepdims=True)).astype(np.float32)


def _alrededor(rng: Any, centroides: Any, grupos: Any) -> Any:
    # Ruido de norma ~RUIDO alrededor de un centroide unitario.
    ruido = rng.standard_normal((len(grupos), DIM)).astype(np.float32) * (RUIDO / np.sqrt(DIM))
    return _unitarios(centroides[grupos] + ruido)


def _binario(v: Any) -> bytes:
    """Formato binario de pgvector: dim int16, 0 int16, float32 big-endian."""
    return struct.pack(">HH", DIM, 0) + v.astype(">f4").tobytes()


def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for tenant, _ in TENANTS:
        for tabla in (
            "cm.nuggets",
            "cm.curation_queue",
            "cm.doc_chunk_links",
            "vec.doc_chunks",
            "core.onboarding_documents",
            "vec.definition_embeddings",
            "cm.concepts",
            "cm.definitions",
            "cm.repo_snapshots",
            "core.users",
        ):
            conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (tenant,))
        conn.execute("DELETE FROM core.organizations WHERE id = %s", (tenant,))


def _cargar_tenant(dsn: str, tenant: str, cuota: float, semilla: int, centroides: Any) -> Any:
    """Siembra un tenant en su propia conexión (los tenants se cargan en paralelo)."""
    rng = np.random.default_rng(semilla)
    n_def = round(N_DEFINICIONES * cuota)
    n_chunks = round(N_CHUNKS * cuota)
    grupos = rng.integers(0, N_GRUPOS, n_def)
    vecs = _alrededor(rng, centroides, grupos)
    # 70 % definiciones, 30 % conceptos (de ellos, uno de cada cinco sin curar: confianza NULL).
    es_concepto = rng.random(n_def) < 0.30
    sin_curar = es_concepto & (rng.random(n_def) < 0.20)
    modulos = [f"src/m{g % N_MODULOS:02d}" for g in grupos]
    ids = [f"bd_{i:06d}" for i in range(n_def)]
    usuario = f"usr_{tenant}"
    documento = f"doc_{tenant}"
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (tenant, tenant)
        )
        conn.execute(
            "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, %s, 'Bench')",
            (usuario, tenant, f"{usuario}@bench.dev"),
        )
        conn.execute(
            "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama)"
            " VALUES (%s, %s, 'sha', 'main')",
            (SNAP, tenant),
        )
        with conn.transaction(), conn.cursor() as cur:
            with cur.copy(
                "COPY cm.definitions (id, tenant_id, modulo, nombre, kind, descripcion, snapshot_id)"
                " FROM STDIN"
            ) as cp:
                for i in np.flatnonzero(~es_concepto):
                    cp.write_row((ids[i], tenant, modulos[i], ids[i], "class", "bench", SNAP))
            with cur.copy(
                "COPY cm.concepts (id, tenant_id, name_en, name_local, descripcion, confianza,"
                " snapshot_id) FROM STDIN"
            ) as cp:
                for i in np.flatnonzero(es_concepto):
                    conf = None if sin_curar[i] else 0.9
                    cp.write_row((ids[i], tenant, ids[i], ids[i], "bench", conf, SNAP))
            with cur.copy(
                "COPY vec.definition_embeddings (id, tenant_id, nodo_ref, capa, nombre, descripcion,"
                " modulo, embedding, texto_hash) FROM STDIN (FORMAT BINARY)"
            ) as cp:
                cp.set_types(["text"] * 7 + ["bytea", "text"])
                for i in range(n_def):
                    capa = "concept" if es_concepto[i] else "definition"
                    cp.write_row(
                        (f"e_{ids[i]}", tenant, ids[i], capa, ids[i], "bench", modulos[i],
                         _binario(vecs[i]), "h")
                    )  # fmt: skip

        # Documentación: chunks alrededor de los mismos grupos, enlazados a una definición de su
        # módulo, y un nugget por cada cuatro chunks (la mitad aprobados).
        conn.execute(
            """INSERT INTO core.onboarding_documents
                   (id, tenant_id, nombre_archivo, content_type, tamano_bytes, blob_ref, subido_por,
                    estado, chunks_count, procesado_en)
               VALUES (%s, %s, 'bench.md', 'text/markdown', 1, %s, %s, 'procesado', %s, now())""",
            (documento, tenant, f"{tenant}/docs/{documento}/bench.md", usuario, n_chunks),
        )
        grupos_c = rng.integers(0, N_GRUPOS, n_chunks)
        vecs_c = _alrededor(rng, centroides, grupos_c)
        defs_por_modulo: dict[str, str] = {}
        for i in np.flatnonzero(~es_concepto):
            defs_por_modulo.setdefault(modulos[i], ids[i])
        with conn.transaction(), conn.cursor() as cur:
            with cur.copy(
                "COPY vec.doc_chunks (id, tenant_id, document_id, chunk_index, content, embedding,"
                " seccion, texto_hash) FROM STDIN (FORMAT BINARY)"
            ) as cp:
                cp.set_types(["text", "text", "text", "int4", "text", "bytea", "text", "text"])
                for j in range(n_chunks):
                    cp.write_row(
                        (
                            f"bc_{j:06d}",
                            tenant,
                            documento,
                            j,
                            "bench",
                            _binario(vecs_c[j]),
                            "S",
                            "h",
                        )
                    )
            enlaces = [
                (tenant, f"bc_{j:06d}", defs_por_modulo[m], "semantico", 0.8)
                for j in range(n_chunks)
                if (m := f"src/m{grupos_c[j] % N_MODULOS:02d}") in defs_por_modulo
            ]
            cur.executemany(
                "INSERT INTO cm.doc_chunk_links (tenant_id, chunk_id, nodo_ref, metodo, score)"
                " VALUES (%s, %s, %s, %s, %s)",
                enlaces,
            )
            cur.executemany(
                "INSERT INTO cm.nuggets (id, tenant_id, regla, tipo, scope_refs, evidencia_ref)"
                " VALUES (%s, %s, %s, 'regla_negocio', '[]'::jsonb, %s)",
                [
                    (f"bn_{j:06d}", tenant, f"Regla {j}", f"bc_{j:06d}")
                    for j in range(0, n_chunks, 4)
                ],
            )
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
        conn.execute(
            "SELECT count(cm.resolver_nugget(n, %s, 'aprobado')) FROM unnest(%s::text[]) AS n",
            (usuario, [f"bn_{j:06d}" for j in range(0, n_chunks, 8)]),  # la mitad
        )
        conn.execute("SELECT set_config('app.tenant_id', '', false)")
    return ids, np.where(es_concepto & ~sin_curar, 1, np.where(es_concepto, 2, 0)), vecs


def construir_corpus(dsn: str) -> Corpus:
    rng = np.random.default_rng(SEMILLA)
    centroides = _unitarios(rng.standard_normal((N_GRUPOS, DIM)).astype(np.float32))
    with psycopg.connect(dsn, autocommit=True) as conn:
        _limpiar(conn)
    corpus = Corpus(centroides=centroides, ids={}, capas={}, vectores={})
    with ThreadPoolExecutor(max_workers=4) as pool:
        futuros = {
            t: pool.submit(_cargar_tenant, dsn, t, cuota, SEMILLA + i + 1, centroides)
            for i, (t, cuota) in enumerate(TENANTS)
        }
        for t, futuro in futuros.items():
            ids, capas, vecs = futuro.result()
            if t in MEDIDOS:
                corpus.ids[t], corpus.capas[t], corpus.vectores[t] = ids, capas, vecs
    with psycopg.connect(dsn, autocommit=True) as conn:
        for tabla in ("vec.definition_embeddings", "vec.doc_chunks", "cm.nuggets", "cm.concepts"):
            conn.execute(f"ANALYZE {tabla}")
        total = conn.execute(
            "SELECT (SELECT count(*) FROM vec.definition_embeddings WHERE tenant_id LIKE 'bench_bus_%')"
            " + (SELECT count(*) FROM vec.doc_chunks WHERE tenant_id LIKE 'bench_bus_%')"
        ).fetchone()
    assert total is not None and total[0] >= 200_000, f"solo se cargaron {total} vectores"
    return corpus


# ---------------------------------------------------------------------------
# Medición
# ---------------------------------------------------------------------------
def _consultas(rng: Any, corpus: Corpus, n: int) -> list[BusquedaHibridaIn]:
    """Mezcla de las formas de uso: todas las capas, por capa, conceptos, documentación y módulo."""
    formas: list[dict[str, Any]] = [
        {},
        {"capas": ["definition"]},
        {"capas": ["concept"]},
        {"capas": ["doc", "nugget"]},
        {"modulo": None},  # se rellena con el módulo del grupo
        {"capas": ["concept"], "incluir_sin_curar": True},
    ]
    grupos = rng.integers(0, N_GRUPOS, n)
    consultas = []
    for i, g in enumerate(grupos):
        q = _alrededor(rng, corpus.centroides, np.array([g]))[0]
        forma = dict(formas[i % len(formas)])
        if "modulo" in forma:
            forma["modulo"] = f"src/m{g % N_MODULOS:02d}"
        consultas.append(BusquedaHibridaIn(embedding=q.tolist(), k=10, **forma))
    return consultas


def _percentil(valores: list[float], p: int) -> float:
    return statistics.quantiles(valores, n=100, method="inclusive")[p - 1]


def forma(c: BusquedaHibridaIn) -> str:
    """Nombre corto de la forma de una consulta (para el desglose de latencias)."""
    partes = ["+".join(x.value for x in c.capas) if c.capas else "todas"]
    if c.modulo:
        partes.append("modulo")
    if c.incluir_sin_curar:
        partes.append("sin_curar")
    return " ".join(partes)


def medir_latencia(dsn_app: str, tenant: str, consultas: list[BusquedaHibridaIn]) -> dict[str, Any]:
    tiempos: list[float] = []
    por_forma: dict[str, list[float]] = {}
    filas = 0
    with GraphLoader(dsn=dsn_app, tenant_id=tenant) as loader:
        for c in consultas[:10]:  # calentamiento: conexión, caché de planes y páginas del índice
            buscar_hibrido(loader, c)
        for c in consultas:
            t0 = time.perf_counter()
            r = buscar_hibrido(loader, c)
            tiempos.append((time.perf_counter() - t0) * 1000)
            por_forma.setdefault(forma(c), []).append(tiempos[-1])
            filas += len(r.resultados)
    return {
        "p50": statistics.median(tiempos),
        "p95": _percentil(tiempos, 95),
        "max": max(tiempos),
        "filas_media": filas / len(consultas),
        "p95_por_forma": {f: _percentil(t, 95) for f, t in sorted(por_forma.items())},
    }


def medir_recall(dsn_app: str, tenant: str, corpus: Corpus, rng: Any) -> float:
    """recall@10 de la capa 'definition' frente al top-10 exacto (numpy) del tenant."""
    ids = corpus.ids[tenant]
    mascara = corpus.capas[tenant] == 0
    vecs = corpus.vectores[tenant][mascara]
    ids_def = [f"e_{i}" for i, m in zip(ids, mascara, strict=True) if m]
    aciertos = 0
    with GraphLoader(dsn=dsn_app, tenant_id=tenant) as loader:
        for g in rng.integers(0, N_GRUPOS, N_CONSULTAS_RECALL):
            q = _alrededor(rng, corpus.centroides, np.array([g]))[0]
            exactos = {ids_def[i] for i in np.argsort(-(vecs @ q), kind="stable")[:10]}
            r = buscar_hibrido(
                loader,
                BusquedaHibridaIn(embedding=q.tolist(), capas=[CapaBusqueda.DEFINITION], k=10),
            )
            aciertos += len(exactos & {x.id for x in r.resultados})
    return aciertos / (10 * N_CONSULTAS_RECALL)


def _nodos(plan: Any) -> Iterator[dict[str, Any]]:
    if isinstance(plan, dict):
        yield plan
        for v in plan.values():
            yield from _nodos(v)
    elif isinstance(plan, list):
        for v in plan:
            yield from _nodos(v)


def inspeccionar_plan(dsn_app: str, tenant: str, consulta: BusquedaHibridaIn) -> dict[str, Any]:
    p = cm_busqueda.parametros_sql(consulta)
    with GraphLoader(dsn=dsn_app, tenant_id=tenant) as loader, loader.fase("explain") as cur:
        cur.execute(
            "SELECT cm.explicar_busqueda_hibrida(%(embedding)s::vector, %(capas)s::text[],"
            " %(modulo)s, %(k)s, %(incluir_sin_curar)s, true)",
            p,
        )
        fila = cur.fetchone()
    assert fila is not None
    recorridos = [
        n
        for n in _nodos(fila[0])
        if n.get("Schema") == "vec"
        and str(n.get("Relation Name", "")).startswith(("definition_embeddings", "doc_chunks"))
    ]
    filtro = f"tenant_id = '{tenant}'::text"
    return {
        "recorridos": [
            {
                "tabla": n["Relation Name"],
                "nodo": n["Node Type"],
                "indice": n.get("Index Name"),
                "filtro_tenant": any(
                    filtro in str(v) for k, v in n.items() if k.endswith(("Filter", "Cond"))
                ),
            }
            for n in recorridos
        ],
        "ms": fila[0][0].get("Execution Time"),
    }


def ejecutar() -> dict[str, Any]:
    dsn = _dsn_admin()
    dsn_app = _dsn_app(dsn)
    inicio = time.perf_counter()
    corpus = construir_corpus(dsn)
    carga_s = time.perf_counter() - inicio
    rng = np.random.default_rng(SEMILLA + 100)
    informe: dict[str, Any] = {"carga_s": carga_s, "tenants": {}}
    try:
        for tenant in MEDIDOS:
            consultas = _consultas(rng, corpus, N_CONSULTAS)
            informe["tenants"][tenant] = {
                "latencia": medir_latencia(dsn_app, tenant, consultas),
                "recall_at_10": medir_recall(dsn_app, tenant, corpus, rng),
                "plan": inspeccionar_plan(dsn_app, tenant, consultas[0]),
            }
    finally:
        if not os.getenv("KC_BENCH_CONSERVAR"):
            with psycopg.connect(dsn, autocommit=True) as conn:
                _limpiar(conn)
    return informe


def informe_markdown(informe: dict[str, Any]) -> str:
    lineas = [
        "## Búsqueda híbrida con 200.000 vectores (KC-CM-08-4)",
        "",
        f"Carga: {informe['carga_s']:.0f} s. Umbral p95 {UMBRAL_P95_MS:.0f} ms (objetivo de diseño "
        f"{OBJETIVO_P95_MS:.0f} ms); recall@10 >= {UMBRAL_RECALL}.",
        "",
        "| tenant | p50 ms | p95 ms | máx ms | filas/consulta | recall@10 | recorridos del plan |",
        "|---|---|---|---|---|---|---|",
    ]
    for tenant, r in informe["tenants"].items():
        lat = r["latencia"]
        recorridos = "; ".join(
            f"{x['tabla']} {x['nodo']}{' ' + x['indice'] if x['indice'] else ''}"
            f"{' (tenant en el plan)' if x['filtro_tenant'] else ' (SIN filtro de tenant)'}"
            for x in r["plan"]["recorridos"]
        )
        lineas.append(
            f"| {tenant} | {lat['p50']:.1f} | {lat['p95']:.1f} | {lat['max']:.1f} | "
            f"{lat['filas_media']:.1f} | {r['recall_at_10']:.3f} | {recorridos} |"
        )
    formas = sorted(
        {f for r in informe["tenants"].values() for f in r["latencia"]["p95_por_forma"]}
    )
    lineas += ["", "p95 por forma de consulta (ms):", ""]
    lineas.append("| tenant | " + " | ".join(formas) + " |")
    lineas.append("|---" * (len(formas) + 1) + "|")
    for tenant, r in informe["tenants"].items():
        p95 = r["latencia"]["p95_por_forma"]
        celdas = " | ".join(f"{p95[f]:.1f}" if f in p95 else "—" for f in formas)
        lineas.append(f"| {tenant} | {celdas} |")
    return "\n".join(lineas) + "\n"


def _guardar(texto: str) -> None:
    destino = os.getenv("KC_BENCH_RESULTADOS")
    if destino:
        Path(destino).parent.mkdir(parents=True, exist_ok=True)
        Path(destino).write_text(texto, encoding="utf-8")


# ---------------------------------------------------------------------------
# Umbrales
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def informe() -> dict[str, Any]:
    datos = ejecutar()
    texto = informe_markdown(datos)
    print("\n" + texto)
    _guardar(texto)
    return datos


@pytest.mark.parametrize("tenant", MEDIDOS)
def test_p95_bajo_400_ms_con_200000_vectores(informe: dict[str, Any], tenant: str) -> None:
    lat = informe["tenants"][tenant]["latencia"]
    assert lat["filas_media"] > 0, "el benchmark debe devolver resultados para ser representativo"
    assert lat["p95"] < UMBRAL_P95_MS, f"p95 = {lat['p95']:.1f} ms (p50 = {lat['p50']:.1f} ms)"


@pytest.mark.parametrize("tenant", MEDIDOS)
def test_recall_at_10_con_filtro_de_capa(informe: dict[str, Any], tenant: str) -> None:
    assert informe["tenants"][tenant]["recall_at_10"] >= UMBRAL_RECALL


@pytest.mark.parametrize("tenant", MEDIDOS)
def test_el_plan_real_filtra_por_tenant(informe: dict[str, Any], tenant: str) -> None:
    recorridos = informe["tenants"][tenant]["plan"]["recorridos"]
    assert recorridos and all(x["filtro_tenant"] for x in recorridos), recorridos


if __name__ == "__main__":  # pragma: no cover
    texto = informe_markdown(ejecutar())
    _guardar(texto)
    sys.stdout.write(texto)
