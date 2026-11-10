"""Búsqueda semántica híbrida contra PostgreSQL 16 + pgvector reales (KC-CM-08-4).

Requiere `make up && make db` (migración 043). Las búsquedas corren como kallicode_app con el
tenant fijado por GraphLoader.fase(), igual que el pipeline; el administrador solo siembra.

Corpus: tests/fixtures/busqueda (10 temas: símbolos, ficheros, tablas, columnas, definiciones,
conceptos, chunks de documentación y nuggets), embebido con el embebedor determinista de
kallicode_codemapping.evaluacion_busqueda. Dos organizaciones con los MISMOS ids y los MISMOS
vectores: si la búsqueda mezclara tenants, la otra saldría igual de cerca.

Criterios:
- P@5 de las veinte consultas de referencia medida y comparada con la línea base versionada
  (docs/benchmarks/busqueda_hibrida_p5_v1.json).
- El filtro por tenant aparece en el plan de ejecución de cada recorrido vectorial (EXPLAIN de la
  consulta real con cm.explicar_busqueda_hibrida).
- Sin petición explícita no se devuelven conceptos ni nuggets sin curar.
La latencia p95 con 200.000 vectores la mide tests/bench_busqueda_hibrida.py (CI nocturno).
"""

from __future__ import annotations

import os
import statistics
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from kallicode_codemapping import evaluacion_busqueda as ev
from kallicode_codemapping.cm_queries import (
    SQL_BUSQUEDA_HIBRIDA,
    buscar_hibrido,
    buscar_por_texto,
    precision_en_k,
)
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_core import cm_busqueda
from kallicode_core.cm_busqueda import BusquedaHibridaIn, BusquedaHibridaOut
from psycopg import errors

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"

TENANT_A = "org_test_bus_a"
TENANT_B = "org_test_bus_b"
TODOS = (TENANT_A, TENANT_B)
SNAP = "snap_bus"

CORPUS = ev.cargar_corpus()
CONSULTAS = ev.cargar_consultas()
EMBEBEDOR = ev.EmbebedorReferencia()


def _esquema_listo() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute(
                """SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'cm' AND p.proname = 'buscar_hibrido'"""
            ).fetchone()
            if not (fila and fila[0]):
                return False
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _esquema_listo(), reason="Postgres no disponible o 043 sin aplicar (make up && make db)"
)


# ---------------------------------------------------------------------------
# Siembra
# ---------------------------------------------------------------------------
def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for t in TODOS:
        for tabla in (
            "cm.nuggets",
            "cm.curation_queue",
            "cm.doc_chunk_links",
            "vec.doc_chunks",
            "core.onboarding_documents",
            "vec.definition_embeddings",
            "cm.edges",
            "cm.concepts",
            "cm.definitions",
            "cm.symbols",
            "cm.files",
            "cm.db_columns",
            "cm.db_tables",
            "cm.snapshot_vigente",
            "cm.repo_snapshots",
            "core.users",
        ):
            conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.organizations WHERE id = %s", (t,))


def _marca(tenant: str, texto: str) -> str:
    """Todo lo sembrado lleva el tenant: así una fuga se ve en el título o el contenido."""
    return f"[{tenant}] {texto}"


def _tabla_de_columna(columna: ev.Elemento) -> ev.Elemento:
    esquema_tabla = columna.titulo.rsplit(".", 1)[0]
    return next(e for e in CORPUS.de_capa("db_table") if e.titulo == esquema_tabla)


def _sembrar(conn: psycopg.Connection[Any], tenant: str, vectores: dict[str, list[float]]) -> None:
    usuario = f"usr_{tenant}"
    documento = f"doc_bus_{tenant}"  # core.onboarding_documents.id es único en toda la base
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (tenant, tenant))
    conn.execute(
        "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, %s, 'Curadora')",
        (usuario, tenant, f"{usuario}@prueba.dev"),
    )
    conn.execute(
        "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) VALUES (%s, %s, 'sha', 'main')",
        (SNAP, tenant),
    )
    conn.execute(
        """INSERT INTO core.onboarding_documents
               (id, tenant_id, nombre_archivo, content_type, tamano_bytes, blob_ref, subido_por,
                estado, chunks_count, procesado_en)
           VALUES (%s, %s, 'manual.md', 'text/markdown', 10, %s, %s, 'procesado', 1, now())""",
        (documento, tenant, f"{tenant}/docs/{documento}/manual.md", usuario),
    )

    # Nodos del grafo (los triggers de 022 los registran en cm.nodes).
    for e in CORPUS.de_capa("file"):
        conn.execute(
            "INSERT INTO cm.files (id, tenant_id, path, language, sha256, snapshot_id) "
            "VALUES (%s, %s, %s, 'python', 'x', %s)",
            (e.id, tenant, e.titulo, SNAP),
        )
    for e in CORPUS.de_capa("symbol"):
        conn.execute(
            "INSERT INTO cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line,"
            " signature) VALUES (%s, %s, %s, 'function', %s, 1, 2, %s)",
            (e.id, tenant, e.fichero, e.titulo, f"def {e.titulo}()"),
        )
    for e in CORPUS.de_capa("db_table"):
        esquema, nombre = e.titulo.split(".", 1)
        conn.execute(
            "INSERT INTO cm.db_tables (id, tenant_id, esquema, nombre, snapshot_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            (e.id, tenant, esquema, nombre, SNAP),
        )
    for e in CORPUS.de_capa("db_column"):
        conn.execute(
            "INSERT INTO cm.db_columns (id, tenant_id, table_id, nombre, tipo, nullable) "
            "VALUES (%s, %s, %s, %s, 'numeric', false)",
            (e.id, tenant, _tabla_de_columna(e).id, e.titulo.rsplit(".", 1)[1]),
        )
    for e in CORPUS.de_capa("definition"):
        conn.execute(
            "INSERT INTO cm.definitions (id, tenant_id, modulo, nombre, kind, descripcion,"
            " snapshot_id) VALUES (%s, %s, %s, %s, 'class', %s, %s)",
            (e.id, tenant, e.modulo, e.titulo, e.texto, SNAP),
        )
    for e in CORPUS.de_capa("concept"):
        curador = e.estado == "curador"
        conn.execute(
            "INSERT INTO cm.concepts (id, tenant_id, name_en, name_local, descripcion, confianza,"
            " curado_por, curado_en, snapshot_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                e.id,
                tenant,
                e.id,
                e.titulo,
                e.texto,
                0.9 if e.estado in ("curado", "pendiente") else None,
                usuario if curador else None,
                "2026-01-01T00:00:00Z" if curador else None,
                SNAP,
            ),
        )
        if e.estado == "pendiente":
            # Bajar la confianza bajo el umbral no se aplica: abre una revisión pendiente (019).
            conn.execute(
                "UPDATE cm.concepts SET confianza = 0.5 WHERE tenant_id = %s AND id = %s",
                (tenant, e.id),
            )

    filas = [
        (
            f"de_{e.id}",
            tenant,
            e.id,
            e.capa,
            _marca(tenant, e.titulo),
            _marca(tenant, e.texto),
            e.modulo,
            cm_busqueda.vector_literal(vectores[e.id]),
            f"h_{e.id}",
        )
        for e in CORPUS.de_capa(*ev.CAPAS_DEFINICION)
    ]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO vec.definition_embeddings (id, tenant_id, nodo_ref, capa, nombre,"
            " descripcion, modulo, embedding, texto_hash)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s)",
            filas,
        )

    # Documentación, enlaces al grafo y nuggets.
    for i, e in enumerate(CORPUS.de_capa("doc")):
        conn.execute(
            """INSERT INTO vec.doc_chunks
                   (id, tenant_id, document_id, chunk_index, content, embedding, seccion, texto_hash)
               VALUES (%s, %s, %s, %s, %s, %s::vector, %s, %s)""",
            (
                e.id,
                tenant,
                documento,
                i,
                _marca(tenant, e.texto),
                cm_busqueda.vector_literal(vectores[e.id]),
                _marca(tenant, e.titulo),
                f"h_{e.id}",
            ),
        )
        for nodo in e.enlaces:
            conn.execute(
                "INSERT INTO cm.doc_chunk_links (tenant_id, chunk_id, nodo_ref, metodo, score) "
                "VALUES (%s, %s, %s, 'lexico', 0.9)",
                (tenant, e.id, nodo),
            )
    nuggets = CORPUS.de_capa("nugget")
    for e in nuggets:
        conn.execute(
            "INSERT INTO cm.nuggets (id, tenant_id, regla, tipo, scope_refs, evidencia_ref, seccion)"
            " VALUES (%s, %s, %s, 'regla_negocio', '[]'::jsonb, %s, 'Sección')",
            (e.id, tenant, _marca(tenant, e.texto), e.chunk),
        )
    conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
    for e in nuggets:
        if e.estado in ("aprobado", "rechazado"):
            conn.execute("SELECT id FROM cm.resolver_nugget(%s, %s, %s)", (e.id, usuario, e.estado))
    conn.execute("SELECT set_config('app.tenant_id', '', false)")


@pytest.fixture(scope="module")
def vectores() -> dict[str, list[float]]:
    return ev.vectores_corpus(CORPUS, EMBEBEDOR)


@pytest.fixture(scope="module")
def corpus_sembrado(vectores: dict[str, list[float]]) -> Iterator[None]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        _sembrar(conn, TENANT_A, vectores)
        _sembrar(conn, TENANT_B, vectores)  # mismos ids y mismos vectores
        conn.execute("ANALYZE vec.definition_embeddings")
        conn.execute("ANALYZE vec.doc_chunks")
        yield
        _limpiar(conn)


def _buscar(tenant: str, **kw: Any) -> BusquedaHibridaOut:
    with GraphLoader(dsn=APP_DSN, tenant_id=tenant) as loader:
        return buscar_hibrido(loader, BusquedaHibridaIn(**kw))


def _vector_texto(texto: str) -> list[float]:
    return EMBEBEDOR.embed_texts_sync([texto])[0]


def _consulta(c: ev.Consulta, k: int = ev.K_PRECISION) -> dict[str, Any]:
    return {
        "embedding": _vector_texto(c.texto),
        "capas": list(c.capas) if c.capas else None,
        "modulo": c.modulo,
        "k": k,
        "incluir_sin_curar": c.incluir_sin_curar,
    }


def _ids(r: BusquedaHibridaOut) -> list[str]:
    return [x.id for x in r.resultados]


def _referencia(r: cm_busqueda.ResultadoBusqueda) -> str:
    """Id del elemento del corpus (vec.definition_embeddings.id es de_<nodo>)."""
    return r.referencia if r.capa.value in ev.CAPAS_DEFINICION else r.id


# ---------------------------------------------------------------------------
# P@5 sobre las veinte consultas de referencia
# ---------------------------------------------------------------------------
def test_la_linea_base_versionada_corresponde_al_corpus_y_las_consultas() -> None:
    base = ev.cargar_linea_base()
    assert base["version"] == ev.VERSION_LINEA_BASE
    assert base["embebedor"] == ev.EMBEBEDOR_REFERENCIA
    assert base["corpus"] == CORPUS.version
    assert base["n_consultas"] == len(CONSULTAS) == 20
    assert set(base["consultas"]) == {c.id for c in CONSULTAS}


def test_p_at_5_de_las_veinte_consultas_no_baja_de_la_linea_base(corpus_sembrado: None) -> None:
    base = ev.cargar_linea_base()
    medidas: dict[str, float] = {}
    for c in CONSULTAS:
        r = _buscar(TENANT_A, **_consulta(c))
        devueltos = [_referencia(x) for x in r.resultados]
        assert all(x.contenido.startswith(f"[{TENANT_A}]") for x in r.resultados), c.id
        medidas[c.id] = precision_en_k(devueltos, ev.relevantes(CORPUS, c), ev.K_PRECISION)

    media = statistics.fmean(medidas.values())
    tabla = "\n".join(
        f"  {q}: P@5 = {p:.2f} (línea base {base['consultas'][q]['p_at_5']:.2f})"
        for q, p in sorted(medidas.items())
    )
    print(f"\nP@5 media = {media:.4f} (línea base {base['p_at_5_media']:.4f})\n{tabla}")
    # La búsqueda sobre un corpus pequeño es exacta: debe reproducir la línea base. Se admite una
    # posición de diferencia (un empate en la quinta posición resuelto distinto por el redondeo a
    # float4 de pgvector), nunca una caída real.
    assert media >= base["p_at_5_media"] - 0.01, tabla
    distintas = [q for q, p in medidas.items() if abs(p - base["consultas"][q]["p_at_5"]) > 1e-9]
    assert len(distintas) <= 1, tabla


def test_buscar_por_texto_embebe_y_busca(corpus_sembrado: None) -> None:
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT_A) as loader:
        r = buscar_por_texto(
            loader,
            EMBEBEDOR,
            "dónde se implementa la facturación por convenio corporativo",
            capas=["symbol", "definition", "doc"],
            k=3,
        )
    assert len(r.resultados) == 3
    assert {_referencia(x) for x in r.resultados} <= ev.relevantes(CORPUS, CONSULTAS[0])


# ---------------------------------------------------------------------------
# Aislamiento multi-tenant: el filtro está en el plan
# ---------------------------------------------------------------------------
TABLAS_VECTORIALES = ("definition_embeddings", "doc_chunks")


def _nodos(plan: Any) -> Iterator[dict[str, Any]]:
    if isinstance(plan, dict):
        yield plan
        for valor in plan.values():
            yield from _nodos(valor)
    elif isinstance(plan, list):
        for valor in plan:
            yield from _nodos(valor)


def _condiciones(nodo: dict[str, Any]) -> list[str]:
    return [str(v) for k, v in nodo.items() if k.endswith(("Filter", "Cond"))]


def _explicar(tenant: str, **kw: Any) -> Any:
    consulta = BusquedaHibridaIn(**kw)
    p = cm_busqueda.parametros_sql(consulta)
    with GraphLoader(dsn=APP_DSN, tenant_id=tenant) as loader, loader.fase("explain") as cur:
        cur.execute(
            "SELECT cm.explicar_busqueda_hibrida(%(embedding)s::vector, %(capas)s::text[],"
            " %(modulo)s, %(k)s, %(incluir_sin_curar)s)",
            p,
        )
        fila = cur.fetchone()
    assert fila is not None
    return fila[0]


@pytest.mark.parametrize(
    ("capas", "modulo", "esperadas"),
    [
        (None, None, {"definition_embeddings", "doc_chunks"}),
        (["symbol", "concept"], None, {"definition_embeddings"}),
        (["doc"], None, {"doc_chunks"}),
        (["nugget"], None, {"doc_chunks"}),
        (None, "src/facturacion", {"definition_embeddings", "doc_chunks"}),
    ],
    ids=["todas", "definiciones", "documentacion", "nuggets", "con-modulo"],
)
def test_el_filtro_por_tenant_aparece_en_el_plan_de_cada_recorrido_vectorial(
    corpus_sembrado: None, capas: list[str] | None, modulo: str | None, esperadas: set[str]
) -> None:
    plan = _explicar(
        TENANT_A,
        embedding=_vector_texto("facturación por convenio"),
        capas=capas,
        modulo=modulo,
    )
    recorridos = [
        n
        for n in _nodos(plan)
        if n.get("Schema") == "vec"
        and str(n.get("Relation Name", "")).startswith(TABLAS_VECTORIALES)
    ]
    vistas = {t for n in recorridos for t in TABLAS_VECTORIALES if n["Relation Name"].startswith(t)}
    assert esperadas <= vistas, f"el plan no recorre {esperadas - vistas}"
    filtro = f"tenant_id = '{TENANT_A}'::text"
    for nodo in recorridos:
        conds = _condiciones(nodo)
        assert any(filtro in c for c in conds), (
            f"{nodo['Node Type']} sobre {nodo['Relation Name']} sin filtro de tenant: {conds}"
        )
        assert not any(TENANT_B in c for c in conds)


def test_el_plan_de_otro_tenant_filtra_por_el_suyo(corpus_sembrado: None) -> None:
    plan = _explicar(TENANT_B, embedding=_vector_texto("cobros"), capas=["doc"])
    conds = [c for n in _nodos(plan) for c in _condiciones(n)]
    assert any(f"tenant_id = '{TENANT_B}'::text" in c for c in conds)
    assert not any(TENANT_A in c for c in conds)


def test_no_hay_fuga_entre_organizaciones_con_los_mismos_ids_y_vectores(
    corpus_sembrado: None,
) -> None:
    consulta = {"embedding": _vector_texto("factura de convenio corporativo"), "k": 100}
    a = _buscar(TENANT_A, **consulta)
    b = _buscar(TENANT_B, **consulta)
    assert _ids(a) == _ids(b)  # mismos ids, mismo orden...
    assert all(f"[{TENANT_A}]" in x.titulo or f"[{TENANT_A}]" in x.contenido for x in a.resultados)
    assert all(f"[{TENANT_B}]" in x.titulo or f"[{TENANT_B}]" in x.contenido for x in b.resultados)
    assert not any(TENANT_B in x.titulo + x.contenido for x in a.resultados)  # ...contenido propio


def test_sin_tenant_en_la_sesion_no_devuelve_nada(corpus_sembrado: None) -> None:
    consulta = BusquedaHibridaIn(embedding=_vector_texto("factura"), k=50)
    with psycopg.connect(APP_DSN) as conn:
        filas = conn.execute(SQL_BUSQUEDA_HIBRIDA, cm_busqueda.parametros_sql(consulta)).fetchall()
    assert filas == []


# ---------------------------------------------------------------------------
# Curación
# ---------------------------------------------------------------------------
def _por_estado(capa: str, *estados: str) -> set[str]:
    return {e.id for e in CORPUS.de_capa(capa) if e.estado in estados}


def test_sin_peticion_explicita_no_devuelve_conceptos_ni_nuggets_sin_curar(
    corpus_sembrado: None,
) -> None:
    r = _buscar(
        TENANT_A, embedding=_vector_texto("reglas y conceptos"), capas=["concept", "nugget"], k=100
    )
    devueltos = {_referencia(x) for x in r.resultados}
    assert devueltos == _por_estado("concept", "curado", "curador") | _por_estado(
        "nugget", "aprobado"
    )
    assert all(x.estado_curacion == "curado" for x in r.resultados)
    assert not r.incluye_sin_curar


def test_con_peticion_explicita_incluye_los_sin_curar_marcados_y_nunca_los_rechazados(
    corpus_sembrado: None,
) -> None:
    r = _buscar(
        TENANT_A,
        embedding=_vector_texto("reglas y conceptos"),
        capas=["concept", "nugget"],
        k=100,
        incluir_sin_curar=True,
    )
    estado = {_referencia(x): x.estado_curacion for x in r.resultados}
    sin_curar = _por_estado("concept", "sin_curar", "pendiente") | _por_estado(
        "nugget", "propuesto"
    )
    curados = _por_estado("concept", "curado", "curador") | _por_estado("nugget", "aprobado")
    assert set(estado) == sin_curar | curados
    assert {i for i, e in estado.items() if e == "sin_curar"} == sin_curar
    assert not set(estado) & _por_estado("nugget", "rechazado")
    assert r.incluye_sin_curar


def test_la_consulta_aplica_la_misma_regla_de_curacion_que_concepto_curado(
    corpus_sembrado: None,
) -> None:
    """La consulta evalúa la curación como conjunto (CTE); debe coincidir con la función."""
    r = _buscar(
        TENANT_A,
        embedding=_vector_texto("conceptos"),
        capas=["concept"],
        k=100,
        incluir_sin_curar=True,
    )
    estados = {x.referencia: x.estado_curacion for x in r.resultados}
    with psycopg.connect(ADMIN_DSN) as conn:
        esperado: dict[str, str] = dict(
            conn.execute(
                "SELECT id, CASE WHEN cm.concepto_curado(tenant_id, id) THEN 'curado'"
                " ELSE 'sin_curar' END FROM cm.concepts WHERE tenant_id = %s",
                (TENANT_A,),
            ).fetchall()
        )
    assert estados == esperado


def test_un_concepto_con_revision_pendiente_esta_sin_curar(corpus_sembrado: None) -> None:
    """Confianza 0,9 en la tabla, pero su bajada a 0,5 dejó una revisión pendiente (019)."""
    pendientes = _por_estado("concept", "pendiente")
    assert pendientes
    with psycopg.connect(ADMIN_DSN) as conn:
        for concepto in pendientes:
            fila = conn.execute(
                "SELECT confianza, cm.concepto_curado(tenant_id, id) FROM cm.concepts"
                " WHERE tenant_id = %s AND id = %s",
                (TENANT_A, concepto),
            ).fetchone()
            assert fila is not None and float(fila[0]) == 0.9 and fila[1] is False


# ---------------------------------------------------------------------------
# Filtros por capa y módulo, orden
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "capas", [["doc"], ["symbol", "file"], ["db_table", "db_column"], ["nugget"], ["definition"]]
)
def test_el_filtro_por_capa_solo_devuelve_esas_capas(
    corpus_sembrado: None, capas: list[str]
) -> None:
    r = _buscar(TENANT_A, embedding=_vector_texto("factura"), capas=capas, k=100)
    assert r.resultados
    assert {x.capa.value for x in r.resultados} <= set(capas)


def test_el_filtro_por_modulo_es_de_carpeta(corpus_sembrado: None) -> None:
    c = ev.Consulta(id="m", tema="fac", texto="factura", modulo="src/facturacion")
    r = _buscar(TENANT_A, embedding=_vector_texto(c.texto), modulo=c.modulo, k=100)
    esperados = {
        e.id for e in CORPUS.elementos.values() if ev.es_elegible(CORPUS, e, c)
    }  # incluye src/facturacion/impuestos
    assert {_referencia(x) for x in r.resultados} == esperados
    assert any(e.startswith("imp_") for e in esperados)
    assert (
        _buscar(TENANT_A, embedding=_vector_texto("factura"), modulo="src/factura").resultados == []
    )


def test_orden_determinista_y_similitud_descendente(corpus_sembrado: None) -> None:
    consulta = {"embedding": _vector_texto("cobro con tarjeta rechazado"), "k": 40}
    primera = _buscar(TENANT_A, **consulta)
    for _ in range(5):
        assert _buscar(TENANT_A, **consulta) == primera
    similitudes = [x.similitud for x in primera.resultados]
    assert similitudes == sorted(similitudes, reverse=True)
    assert len(primera.resultados) == 40


# ---------------------------------------------------------------------------
# Parámetros y configuración HNSW
# ---------------------------------------------------------------------------
def test_la_busqueda_no_altera_los_parametros_hnsw_de_la_transaccion(
    corpus_sembrado: None,
) -> None:
    p = cm_busqueda.parametros_sql(BusquedaHibridaIn(embedding=_vector_texto("factura")))
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT_A) as loader, loader.fase("guc") as cur:
        cur.execute("SELECT set_config('hnsw.ef_search', '77', true)")
        cur.execute("SELECT set_config('hnsw.max_scan_tuples', '1234', true)")
        cur.execute(SQL_BUSQUEDA_HIBRIDA, p)
        assert cur.fetchall()
        cur.execute(
            "SELECT current_setting('hnsw.ef_search'), current_setting('hnsw.max_scan_tuples')"
        )
        assert cur.fetchone() == ("77", "1234")


@pytest.mark.parametrize(
    ("sql", "params", "mensaje"),
    [
        ("SELECT * FROM cm.buscar_hibrido('[1,2,3]'::vector)", (), "1024 dimensiones"),
        ("SELECT * FROM cm.buscar_hibrido(%s::vector, NULL, NULL, 0)", ("v",), "k debe"),
        ("SELECT * FROM cm.buscar_hibrido(%s::vector, NULL, NULL, 101)", ("v",), "k debe"),
        ("SELECT * FROM cm.buscar_hibrido(%s::vector, ARRAY['x'])", ("v",), "capas desconocidas"),
        ("SELECT * FROM cm.buscar_hibrido(%s::vector, ARRAY[]::text[])", ("v",), "vacía"),
    ],
    ids=["dimensiones", "k-cero", "k-excesivo", "capa-desconocida", "capas-vacias"],
)
def test_parametros_invalidos_fallan_con_un_mensaje_claro(
    corpus_sembrado: None, sql: str, params: tuple[str, ...], mensaje: str
) -> None:
    vector = cm_busqueda.vector_literal(_vector_texto("factura"))
    with (
        psycopg.connect(APP_DSN) as conn,
        pytest.raises(errors.InvalidParameterValue, match=mensaje),
    ):
        conn.execute(sql, tuple(vector for _ in params))
