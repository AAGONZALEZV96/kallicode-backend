"""Resiliencia de la capa de consulta y de la ingesta ante caídas reales (KC-CM-11-5).

Requiere PostgreSQL 16 con el esquema aplicado (`make up && make db`). Las caídas de la base son
reales: se termina el backend de la conexión con `pg_terminate_backend`, que es lo que ve el
cliente cuando PostgreSQL se reinicia o un failover corta las conexiones. TEI y el LLM se
sustituyen por dobles que fallan como lo hace el cliente real (httpx y `EscaladoSemantico`).

Cubre:
- Paridad: la API interna (`ConsultaGrafoSesion`, SQLAlchemy async) y el cliente de los agentes
  (`ConsultaGrafoPostgres`, psycopg) responden lo mismo en las diez operaciones del contrato.
- Base caída: cada operación da `DEPENDENCIA_NO_DISPONIBLE` (503) y no la excepción del driver; al
  volver la base, el mismo cliente responde sin reiniciarlo.
- TEI caído: el grafo sigue respondiendo, la búsqueda por texto da el error tipificado y
  {{incidents}} se resuelve sin orden semántico.
- LLM caído: la ingesta falla con `EscaladoSemantico` y el grafo publicado sigue sirviéndose.
- Ingesta interrumpida por la base: se reanuda desde su checkpoint sin volver a llamar al LLM ni a
  reservar presupuesto.
"""

from __future__ import annotations

import asyncio
import os
import random
import subprocess
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import patch

import httpx
import psycopg
import pytest
from kallicode_codemapping import build_graph, cm_queries
from kallicode_codemapping.budget import EstimacionPresupuesto
from kallicode_codemapping.config import Settings
from kallicode_codemapping.consulta_grafo import ConsultaGrafoPostgres
from kallicode_codemapping.escalacion import EscaladoSemantico
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_core.cm_busqueda import BusquedaHibridaIn
from kallicode_core.cm_consulta import ConsultaNuggetsIn
from kallicode_core.cm_grafo import OPERACIONES
from kallicode_core.cm_grafo_doble import GrafoEnMemoria, grafo_sintetico
from kallicode_core.cm_grafo_sesion import ConsultaGrafoSesion
from kallicode_core.cm_impacto import ConsultaGrafoIn, ResolverNodosIn, VecinosIn
from kallicode_core.cm_incidentes import DIMENSION_EMBEDDING, ConsultaIncidentesIn
from kallicode_core.errors import AppError
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"
# La API se conecta como kallicode_app con asyncpg (KC_DATABASE_URL en el CI).
APP_ASYNC_DSN = os.getenv("KC_DATABASE_URL") or (
    "postgresql+asyncpg://kallicode_app:dev@localhost:5432/kallicode"
)

TENANT = "org_test_resiliencia"
TENANT_BUILD = "org_test_resiliencia_build"
SNAPSHOT = "snap_resiliencia"
GRAFO = grafo_sintetico(20261116)
N_INCIDENTES = 120


def _esquema_listo() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute(
                "SELECT to_regprocedure('cm.impacto(text[],text[],integer,text[],text,integer,text)')"
                " IS NOT NULL AND to_regclass('cm.incident_nodes') IS NOT NULL"
            ).fetchone()
            return bool(fila and fila[0])
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _esquema_listo(), reason="Postgres no disponible o esquema sin aplicar (make up && make db)"
)


# ---------------------------------------------------------------------------
# Siembra
# ---------------------------------------------------------------------------
def _unitario(i: int) -> list[float]:
    v = [0.0] * DIMENSION_EMBEDDING
    v[i % DIMENSION_EMBEDDING] = 1.0
    return v


def _literal(v: list[float]) -> str:
    return "[" + ",".join(str(x) for x in v) + "]"


def _limpiar(conn: psycopg.Connection[Any], tenant: str) -> None:
    for tabla in (
        "vec.incident_embeddings",
        "cm.incident_nodes",
        "cm.incidents",
        "vec.definition_embeddings",
        "cm.curation_queue",
        "cm.edges",
        "cm.symbols",
        "cm.files",
        "cm.db_tables",
        "cm.definitions",
        "cm.concepts",
        "cm.nodes",
        "cm.snapshot_vigente",
        "cm.repo_snapshots",
    ):
        conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (tenant,))
    conn.execute("DELETE FROM core.organizations WHERE id = %s", (tenant,))


def _sembrar(conn: psycopg.Connection[Any], grafo: GrafoEnMemoria) -> None:
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (TENANT, TENANT))
    conn.execute(
        "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) "
        "VALUES (%s, %s, 'sha', 'main')",
        (SNAPSHOT, TENANT),
    )
    rnd = random.Random(7)
    nodos = sorted(grafo.kinds)
    with conn.cursor() as cur:
        with cur.copy(
            "COPY cm.files (id, tenant_id, path, language, sha256, snapshot_id) FROM STDIN"
        ) as cp:
            for fid, path, _ in grafo.ficheros:
                cp.write_row((fid, TENANT, path, "python", "h", SNAPSHOT))
        with cur.copy(
            "COPY cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, "
            "signature) FROM STDIN"
        ) as cp:
            for fid, _, simbolos in grafo.ficheros:
                for i, sid in enumerate(simbolos):
                    cp.write_row((sid, TENANT, fid, "function", sid, i + 1, i + 1, "def"))
        with cur.copy("COPY cm.db_tables (id, tenant_id, esquema, nombre) FROM STDIN") as cp:
            for tid in grafo.tablas:
                cp.write_row((tid, TENANT, "public", tid))
        with cur.copy(
            "COPY cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id) FROM STDIN"
        ) as cp:
            for tipo, src, dst in grafo.aristas:
                cp.write_row((TENANT, tipo, grafo.kinds[src], src, grafo.kinds[dst], dst))
        incidentes = [
            (f"inc_{i:04d}", sorted(set(rnd.sample(nodos, rnd.randint(1, 3)))))
            for i in range(N_INCIDENTES)
        ]
        with cur.copy(
            "COPY cm.incidents (id, tenant_id, origen, resumen, nodos_afectados) FROM STDIN"
        ) as cp:
            for iid, afectados in incidentes:
                lista = "[" + ",".join(f'"{n}"' for n in afectados) + "]"
                cp.write_row((iid, TENANT, "manual", f"Fallo {iid}", lista))
        with cur.copy("COPY cm.incident_nodes (tenant_id, incident_id, nodo_ref) FROM STDIN") as cp:
            for iid, afectados in incidentes:
                for n in afectados:
                    cp.write_row((TENANT, iid, n))
        with cur.copy(
            "COPY vec.incident_embeddings (tenant_id, incident_id, embedding, texto_hash) "
            "FROM STDIN"
        ) as cp:
            for i, (iid, _) in enumerate(incidentes):
                cp.write_row((TENANT, iid, _literal(_unitario(i % 8)), "h"))
    conn.execute("ANALYZE cm.edges")


@pytest.fixture(scope="module")
def sembrado() -> Iterator[None]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn, TENANT)
        _sembrar(conn, GRAFO)
        yield
        _limpiar(conn, TENANT)


def _simbolos(n: int, semilla: int) -> list[str]:
    todos = sorted(s for _, _, ss in GRAFO.ficheros for s in ss)
    return random.Random(semilla).sample(todos, n)


def _consultas(simbolo: str, i: int) -> dict[str, BaseModel]:
    """Una consulta por operación del contrato (las diez de OPERACIONES)."""
    fichero = next(path for _, path, ss in GRAFO.ficheros if simbolo in ss)
    return {
        "resolver_nodos": ResolverNodosIn(ids=[simbolo, "no_existe"]),
        "semillas": ConsultaGrafoIn(ficheros=[fichero], nodos=[simbolo]),
        "impacto": ConsultaGrafoIn(nodos=[simbolo], k=3),
        "dependencias": ConsultaGrafoIn(nodos=[simbolo], k=3),
        "subgrafo": ConsultaGrafoIn(nodos=[simbolo], k=2),
        "vecinos": VecinosIn(nodo=simbolo),
        "dependencias_ocultas": ConsultaGrafoIn(nodos=[simbolo], k=3),
        "buscar_hibrido": BusquedaHibridaIn(embedding=_unitario(i)),
        "resolver_nuggets": ConsultaNuggetsIn(variable="nuggets", nodos=[simbolo]),
        "resolver_incidentes": ConsultaIncidentesIn(
            nodos=[simbolo], saltos=2, embedding=_unitario(i % 8) if i % 2 else None
        ),
    }


def _dump(respuesta: Any) -> Any:
    if isinstance(respuesta, list):
        return [r.model_dump(mode="json") for r in respuesta]
    assert isinstance(respuesta, BaseModel)
    return respuesta.model_dump(mode="json")


def _terminar_backend(pid: int) -> None:
    """Corta la conexión desde el servidor, como un reinicio de PostgreSQL o un failover."""
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (pid,))


# ---------------------------------------------------------------------------
# Paridad API interna (async) <-> cliente de los agentes (psycopg)
# ---------------------------------------------------------------------------
async def _respuestas_async(consultas: list[dict[str, BaseModel]]) -> list[dict[str, Any]]:
    motor = create_async_engine(APP_ASYNC_DSN)
    salida: list[dict[str, Any]] = []
    try:
        for porcion in consultas:
            respuestas: dict[str, Any] = {}
            for nombre, consulta in porcion.items():
                # Una transacción por operación, como un request de la API (db.sesion_tenant).
                async with motor.connect() as conn, conn.begin():
                    await conn.execute(
                        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT}
                    )
                    from sqlalchemy.ext.asyncio import AsyncSession

                    db = AsyncSession(bind=conn)
                    respuestas[nombre] = _dump(
                        await getattr(ConsultaGrafoSesion(db), nombre)(consulta)
                    )
            salida.append(respuestas)
    finally:
        await motor.dispose()
    return salida


def test_api_y_cliente_de_agentes_responden_lo_mismo(sembrado: None) -> None:
    consultas = [_consultas(s, i) for i, s in enumerate(_simbolos(12, 3))]
    assert set(consultas[0]) == {o.nombre for o in OPERACIONES}

    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        real = ConsultaGrafoPostgres(loader)
        sync = [
            {nombre: _dump(getattr(real, nombre)(c)) for nombre, c in porcion.items()}
            for porcion in consultas
        ]
    asincrono = asyncio.run(_respuestas_async(consultas))
    assert asincrono == sync
    # La comparación no es trivial: hay recorridos con resultados.
    assert any(r["impacto"]["nodos"] for r in sync)
    assert any(r["resolver_incidentes"]["incidentes"] for r in sync)


# ---------------------------------------------------------------------------
# Base de datos caída
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("operacion", sorted(o.nombre for o in OPERACIONES))
def test_base_caida_da_error_tipificado_y_el_cliente_se_recupera(
    sembrado: None, operacion: str
) -> None:
    consulta = _consultas(_simbolos(1, 11)[0], 1)[operacion]
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        real = ConsultaGrafoPostgres(loader)
        esperado = _dump(getattr(real, operacion)(consulta))

        _terminar_backend(loader.conn.info.backend_pid)
        with pytest.raises(AppError) as info:
            getattr(real, operacion)(consulta)
        assert info.value.codigo == "DEPENDENCIA_NO_DISPONIBLE"
        assert info.value.http == 503
        assert info.value.detalle == {"servicio": "base de datos"}
        assert isinstance(info.value.__cause__, psycopg.OperationalError)

        # La base vuelve (aquí nunca se fue: solo se cortó la conexión). El mismo cliente
        # reabre la conexión en la siguiente consulta, sin reiniciar al agente.
        assert _dump(getattr(real, operacion)(consulta)) == esperado


def test_un_error_de_la_consulta_no_se_disfraza_de_caida(sembrado: None) -> None:
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        real = ConsultaGrafoPostgres(loader)
        with (
            patch.object(cm_queries, "CONSULTAS_IMPACTO", {"vecinos": "SELEC roto"}),
            pytest.raises(psycopg.errors.SyntaxError),
        ):
            real.vecinos(VecinosIn(nodo="x"))


# ---------------------------------------------------------------------------
# TEI caído
# ---------------------------------------------------------------------------
class _TEICaido:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.llamadas = 0

    def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
        self.llamadas += 1
        raise self.exc


_PETICION_TEI = httpx.Request("POST", "http://tei:80/embed")
CAIDAS_TEI: list[Exception] = [
    httpx.ConnectError("connection refused", request=_PETICION_TEI),
    httpx.ReadTimeout("timed out", request=_PETICION_TEI),
    httpx.HTTPStatusError(
        "503", request=_PETICION_TEI, response=httpx.Response(503, request=_PETICION_TEI)
    ),
    httpx.HTTPStatusError(
        "429", request=_PETICION_TEI, response=httpx.Response(429, request=_PETICION_TEI)
    ),
]


@pytest.mark.parametrize("caida", CAIDAS_TEI, ids=["conexion", "timeout", "503", "429"])
def test_tei_caido_degrada_sin_tumbar_el_grafo(sembrado: None, caida: Exception) -> None:
    simbolo = _simbolos(1, 5)[0]
    tei = _TEICaido(caida)
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        # 1. El grafo no depende de TEI: impacto sigue respondiendo.
        recorrido = ConsultaGrafoPostgres(loader).impacto(ConsultaGrafoIn(nodos=[simbolo], k=3))
        assert recorrido.nodos

        # 2. La búsqueda por texto no puede funcionar sin embedding: error del catálogo.
        with pytest.raises(AppError) as info:
            cm_queries.buscar_por_texto(loader, tei, "facturación por convenio")
        assert info.value.codigo == "DEPENDENCIA_NO_DISPONIBLE"
        assert info.value.detalle == {"servicio": "embeddings"}

        # 3. {{incidents}} se resuelve igual, sin orden semántico.
        sin_tei = cm_queries.contexto_incidentes(
            loader, nodos=[simbolo], texto="el cobro falla", embebedor=tei
        )
        sin_texto = cm_queries.contexto_incidentes(loader, nodos=[simbolo])
    assert sin_tei == sin_texto
    assert tei.llamadas == 2


def test_un_400_de_tei_no_es_una_caida(sembrado: None) -> None:
    error = httpx.HTTPStatusError(
        "400", request=_PETICION_TEI, response=httpx.Response(400, request=_PETICION_TEI)
    )
    with (
        GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader,
        pytest.raises(httpx.HTTPStatusError),
    ):
        cm_queries.buscar_por_texto(loader, _TEICaido(error), "consulta")


# ---------------------------------------------------------------------------
# Ingesta: LLM caído y caída de la base a mitad de pipeline
# ---------------------------------------------------------------------------
@pytest.fixture
def repo_git(tmp_path: Any) -> str:
    origen = tmp_path / "origen"
    origen.mkdir()
    (origen / "a.py").write_text("def foo():\n    return bar()\n\ndef bar():\n    return 1\n")
    (origen / "b.py").write_text("def baz():\n    return 2\n")
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@t.local"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "init"],
    ):
        subprocess.run(cmd, cwd=origen, check=True)
    return str(origen)


@pytest.fixture
def tenant_build() -> Iterator[str]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn, TENANT_BUILD)
        conn.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)",
            (TENANT_BUILD, "resiliencia build"),
        )
    yield TENANT_BUILD
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn, TENANT_BUILD)


class _Contadores:
    """Dobles del LLM, de TEI y del presupuesto que cuentan cuántas veces se les llama."""

    def __init__(self) -> None:
        self.llamadas: dict[str, int] = {
            "describe_symbols": 0,
            "consolidate_definitions": 0,
            "induce_ontology": 0,
            "link_definitions": 0,
            "verificar_presupuesto": 0,
            "tei": 0,
        }

    def parches(self) -> list[Any]:
        llamadas = self.llamadas

        def describe_symbols(settings: Any, symbols: Any) -> list[dict[str, Any]]:
            llamadas["describe_symbols"] += 1
            return [
                {"id": s.id, "description_en": f"d {s.name}", "role": "domain"} for s in symbols
            ]

        def consolidate_definitions(
            settings: Any, module: str, described: list[dict[str, Any]]
        ) -> list[dict[str, Any]]:
            llamadas["consolidate_definitions"] += 1
            return [
                {
                    "id": f"def-{module}",
                    "name_en": f"Def {module}",
                    "kind": "capability",
                    "description_en": "d",
                    "member_symbol_ids": [d["id"] for d in described],
                }
            ]

        def induce_ontology(definitions: Any, docs_excerpts: Any = None, **_: Any) -> Any:
            llamadas["induce_ontology"] += 1
            return {
                "concepts": [
                    {"name_en": "T", "name_local": "T", "description_en": "x", "confidence": 0.9}
                ],
                "relations": [],
            }

        def link_definitions(definitions: Any, concepts: Any, **_: Any) -> Any:
            llamadas["link_definitions"] += 1
            return [
                {"definition_id": d["id"], "concept_name_en": "T", "confidence": 0.9}
                for d in definitions
            ], []

        def verificar(**kwargs: Any) -> EstimacionPresupuesto:
            # Se cuenta la reserva, no se calcula: la consulta real usa core.ciclo_inicio, que no
            # existe en el esquema (igual que en bench_ingesta_escala.py).
            llamadas["verificar_presupuesto"] += 1
            return EstimacionPresupuesto(
                total_tokens_estimados=100,
                prompt_tokens_estimados=80,
                completion_tokens_estimados=20,
                costo_usd_estimado=0.0,
                cuota_disponible=1_000_000,
                presupuesto_suficiente=True,
            )

        class TEI:
            def __init__(self, base_url: Any = None) -> None:
                pass

            def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
                llamadas["tei"] += 1
                return [_unitario(i) for i in range(len(texts))]

        return [
            patch.object(build_graph, "describe_symbols", describe_symbols),
            patch.object(build_graph, "consolidate_definitions", consolidate_definitions),
            patch.object(build_graph, "induce_ontology", induce_ontology),
            patch.object(build_graph, "link_definitions", link_definitions),
            patch.object(build_graph, "verificar_presupuesto_tenant", verificar),
            patch.object(build_graph, "TEIEmbeddingClient", TEI),
        ]

    def llm(self) -> int:
        return sum(
            self.llamadas[k]
            for k in (
                "describe_symbols",
                "consolidate_definitions",
                "induce_ontology",
                "link_definitions",
            )
        )


def _settings(tenant: str, repo: str, workdir: str) -> Settings:
    return Settings(tenant_id=tenant, repo_url=repo, workdir=workdir, store_database_url=ADMIN_DSN)


def _snapshots(tenant: str) -> list[dict[str, Any]]:
    with psycopg.connect(ADMIN_DSN) as conn, conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "SELECT id, estado, fase_actual FROM cm.repo_snapshots WHERE tenant_id = %s",
            (tenant,),
        )
        return list(cur.fetchall())


def _ejecutar(settings: Settings, contadores: _Contadores, extra: list[Any] | None = None) -> None:
    parches = contadores.parches() + (extra or [])
    for p in parches:
        p.start()
    try:
        build_graph.full_build(settings)
    finally:
        for p in reversed(parches):
            p.stop()


def _caida_en(metodo: str) -> Any:
    """Corta la conexión del loader justo antes de `metodo`: la caída llega a mitad de fase."""
    original: Callable[..., Any] = getattr(GraphLoader, metodo)

    def _con_caida(self: GraphLoader, *args: Any, **kwargs: Any) -> Any:
        _terminar_backend(self.conn.info.backend_pid)
        return original(self, *args, **kwargs)

    return patch.object(GraphLoader, metodo, _con_caida)


@pytest.mark.parametrize(
    ("metodo", "fase"),
    [("load_curation_queue", "tras el LLM"), ("upsert_vectors", "en la persistencia final")],
)
def test_ingesta_interrumpida_por_la_base_reanuda_sin_repetir_el_llm(
    tenant_build: str, repo_git: str, tmp_path: Any, metodo: str, fase: str
) -> None:
    settings = _settings(tenant_build, repo_git, str(tmp_path / "workdir"))
    contadores = _Contadores()

    # 1. Primer intento: la base corta la conexión de la ingesta (fase: {fase}).
    with pytest.raises(psycopg.OperationalError):
        _ejecutar(settings, contadores, [_caida_en(metodo)])
    snaps = _snapshots(tenant_build)
    assert [s["estado"] for s in snaps] == ["fallido"]
    llm_primer_intento = contadores.llm()
    # describe + consolidar 2 módulos (a.py y b.py) + ontología + enlaces
    assert llm_primer_intento == 5
    assert contadores.llamadas["verificar_presupuesto"] == 1

    # 2. Segundo intento con la base de vuelta: mismo snapshot, completo, sin pagar otra vez.
    _ejecutar(settings, contadores)
    snaps_final = _snapshots(tenant_build)
    assert len(snaps_final) == 1
    assert snaps_final[0]["id"] == snaps[0]["id"]
    assert snaps_final[0]["estado"] == "completo"
    assert contadores.llm() == llm_primer_intento, "el reintento volvió a llamar al LLM"
    assert contadores.llamadas["verificar_presupuesto"] == 1, "volvió a reservar presupuesto"

    # 3. Y el grafo quedó igual que si no hubiera caído: los tres símbolos y sus definiciones.
    with psycopg.connect(ADMIN_DSN) as conn:
        simbolos = conn.execute(
            "SELECT count(*) FROM cm.symbols WHERE tenant_id = %s", (tenant_build,)
        ).fetchone()
        definiciones = conn.execute(
            "SELECT count(*) FROM cm.definitions WHERE tenant_id = %s", (tenant_build,)
        ).fetchone()
    assert simbolos == (3,)
    assert definiciones is not None and definiciones[0] >= 1
    # El checkpoint se limpia al completar.
    assert not (tmp_path / "workdir" / tenant_build / "checkpoints").exists() or not any(
        (tmp_path / "workdir" / tenant_build / "checkpoints").iterdir()
    )


def test_llm_caido_falla_la_ingesta_tipificada_y_el_grafo_sigue_sirviendo(
    sembrado: None, tenant_build: str, repo_git: str, tmp_path: Any
) -> None:
    settings = _settings(tenant_build, repo_git, str(tmp_path / "workdir"))
    contadores = _Contadores()
    caida = patch.object(
        build_graph,
        "describe_symbols",
        side_effect=EscaladoSemantico("describir_simbolos", "tiers agotados", [], 0),
    )
    with pytest.raises(EscaladoSemantico):
        _ejecutar(settings, contadores, [caida])
    assert [s["estado"] for s in _snapshots(tenant_build)] == ["fallido"]

    # Mientras el LLM está caído, la capa de consulta sigue sirviendo el grafo publicado.
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        simbolo = _simbolos(1, 9)[0]
        assert ConsultaGrafoPostgres(loader).impacto(ConsultaGrafoIn(nodos=[simbolo], k=3)).nodos
