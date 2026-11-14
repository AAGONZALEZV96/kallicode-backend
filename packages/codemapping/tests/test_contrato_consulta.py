"""Prueba de contrato de cm-grafo-v1 contra PostgreSQL 16 real (KC-CM-09-4).

Requiere `make up && make db`. Sobre el grafo sintético de referencia (~4.350 nodos, ~21.000
aristas, con ciclos) y 2.000 incidentes:

- El cliente real (`ConsultaGrafoPostgres`) cumple el protocolo `ConsultaGrafo` y sus respuestas
  validan contra el JSON Schema publicado en contratos/cm-grafo-v1.json.
- El doble que se entrega a Backend y Testing (`ConsultaGrafoDoble`) responde EXACTAMENTE lo mismo
  que PostgreSQL sobre el mismo grafo, operación por operación.
- p95 de cada operación por debajo del presupuesto declarado en el contrato (bloquea el merge).
"""

from __future__ import annotations

import os
import random
import statistics
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import jsonschema
import psycopg
import pytest
from kallicode_codemapping.consulta_grafo import ConsultaGrafoPostgres
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_core import cm_grafo
from kallicode_core.cm_busqueda import BusquedaHibridaIn
from kallicode_core.cm_consulta import ConsultaNuggetsIn
from kallicode_core.cm_grafo import ConsultaGrafo
from kallicode_core.cm_grafo_doble import (
    ConsultaGrafoDoble,
    GrafoEnMemoria,
    IncidenteDoble,
    grafo_sintetico,
)
from kallicode_core.cm_impacto import ConsultaGrafoIn, ResolverNodosIn, VecinosIn
from kallicode_core.cm_incidentes import DIMENSION_EMBEDDING, ConsultaIncidentesIn
from pydantic import BaseModel

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"

TENANT = "org_test_contrato_grafo"
TENANT_OTRO = "org_test_contrato_grafo_b"  # mismos ids, otra topología y otros incidentes
SNAPSHOT = "snap_contrato"
GRAFO = grafo_sintetico(20261113)
GRAFO_OTRO = grafo_sintetico(11)
N_INCIDENTES = 2_000
AHORA = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
CONTRATO = cm_grafo.generar_contrato()


def _esquema_listo() -> bool:
    try:
        with psycopg.connect(ADMIN_DSN, connect_timeout=3) as conn:
            fila = conn.execute(
                "SELECT to_regclass('cm.incident_nodes') IS NOT NULL "
                "AND to_regprocedure('cm.buscar_hibrido(vector,text[],text,integer,boolean)') "
                "IS NOT NULL"
            ).fetchone()
            if not (fila and fila[0]):
                return False
        with psycopg.connect(APP_DSN, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _esquema_listo(), reason="Postgres no disponible o esquema sin aplicar (make up && make db)"
)


# ---------------------------------------------------------------------------
# Siembra: el mismo grafo e incidentes en PostgreSQL y en el doble
# ---------------------------------------------------------------------------
def _unitario(i: int) -> list[float]:
    v = [0.0] * DIMENSION_EMBEDDING
    v[i % DIMENSION_EMBEDDING] = 1.0
    return v


def _incidentes(grafo: GrafoEnMemoria, semilla: int) -> list[IncidenteDoble]:
    """Incidentes enlazados a 1-3 nodos; embeddings e_i (coseno exacto 0 o 1) y fechas únicas."""
    rnd = random.Random(semilla)
    nodos = sorted(grafo.kinds)
    return [
        IncidenteDoble(
            id=f"inc_{i:05d}",
            resumen=f"Fallo {i} en {rnd.choice(nodos)}",
            nodos=tuple(sorted(set(rnd.sample(nodos, rnd.randint(1, 3))))),
            causa=f"causa {i}" if i % 3 else None,
            correccion=f"fix {i}" if i % 2 else None,
            creado_en=AHORA - timedelta(minutes=i),
            embedding=tuple(_unitario(rnd.randrange(8))) if i % 4 else None,
        )
        for i in range(N_INCIDENTES)
    ]


INCIDENTES = _incidentes(GRAFO, 1)
INCIDENTES_OTRO = _incidentes(GRAFO_OTRO, 2)


def _limpiar(conn: psycopg.Connection[Any]) -> None:
    for t in (TENANT, TENANT_OTRO):
        for tabla in (
            "vec.incident_embeddings",
            "cm.incident_nodes",
            "cm.incidents",
            "cm.edges",
            "cm.symbols",
            "cm.files",
            "cm.db_tables",
            "cm.nodes",
            "cm.snapshot_vigente",
            "cm.repo_snapshots",
        ):
            conn.execute(f"DELETE FROM {tabla} WHERE tenant_id = %s", (t,))
        conn.execute("DELETE FROM core.organizations WHERE id = %s", (t,))


def _sembrar(
    conn: psycopg.Connection[Any],
    tenant: str,
    grafo: GrafoEnMemoria,
    incidentes: list[IncidenteDoble],
) -> None:
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (tenant, tenant))
    conn.execute(
        "INSERT INTO cm.repo_snapshots (id, tenant_id, commit_sha, rama) "
        "VALUES (%s, %s, 'sha', 'main')",
        (SNAPSHOT, tenant),
    )
    with conn.cursor() as cur:
        with cur.copy(
            "COPY cm.files (id, tenant_id, path, language, sha256, snapshot_id) FROM STDIN"
        ) as cp:
            for fid, path, _ in grafo.ficheros:
                cp.write_row((fid, tenant, path, "python", "h", SNAPSHOT))
        with cur.copy(
            "COPY cm.symbols (id, tenant_id, file_id, kind, name, start_line, end_line, "
            "signature) FROM STDIN"
        ) as cp:
            for fid, _, simbolos in grafo.ficheros:
                for i, sid in enumerate(simbolos):
                    cp.write_row((sid, tenant, fid, "function", sid, i + 1, i + 1, "def"))
        with cur.copy("COPY cm.db_tables (id, tenant_id, esquema, nombre) FROM STDIN") as cp:
            for tid in grafo.tablas:
                cp.write_row((tid, tenant, "public", tid))
        with cur.copy(
            "COPY cm.edges (tenant_id, tipo, src_kind, src_id, dst_kind, dst_id) FROM STDIN"
        ) as cp:
            for tipo, src, dst in grafo.aristas:
                cp.write_row((tenant, tipo, grafo.kinds[src], src, grafo.kinds[dst], dst))
        with cur.copy(
            "COPY cm.incidents (id, tenant_id, origen, resumen, causa, correccion, "
            "nodos_afectados, creado_en) FROM STDIN"
        ) as cp:
            for inc in incidentes:
                cp.write_row(
                    (
                        inc.id,
                        tenant,
                        inc.origen,
                        inc.resumen,
                        inc.causa,
                        inc.correccion,
                        "[" + ",".join(f'"{n}"' for n in inc.nodos) + "]",
                        inc.creado_en,
                    )
                )
        with cur.copy("COPY cm.incident_nodes (tenant_id, incident_id, nodo_ref) FROM STDIN") as cp:
            for inc in incidentes:
                for n in inc.nodos:
                    cp.write_row((tenant, inc.id, n))
        with cur.copy(
            "COPY vec.incident_embeddings (tenant_id, incident_id, embedding, texto_hash) "
            "FROM STDIN"
        ) as cp:
            for inc in incidentes:
                if inc.embedding is not None:
                    literal = "[" + ",".join(str(x) for x in inc.embedding) + "]"
                    cp.write_row((tenant, inc.id, literal, "h"))
    conn.execute("ANALYZE cm.edges")
    conn.execute("ANALYZE cm.incident_nodes")


def _doble(grafo: GrafoEnMemoria, incidentes: list[IncidenteDoble]) -> ConsultaGrafoDoble:
    doble = ConsultaGrafoDoble(grafo)
    for inc in incidentes:
        doble.registrar_incidente(inc)
    return doble


@pytest.fixture(scope="module")
def sembrado() -> Iterator[None]:
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn:
        _limpiar(conn)
        _sembrar(conn, TENANT, GRAFO, INCIDENTES)
        _sembrar(conn, TENANT_OTRO, GRAFO_OTRO, INCIDENTES_OTRO)
        yield
        _limpiar(conn)


def _dump(respuesta: Any) -> Any:
    if isinstance(respuesta, list):
        return [r.model_dump(mode="json") for r in respuesta]
    assert isinstance(respuesta, BaseModel)
    return respuesta.model_dump(mode="json")


def _simbolos(n: int, semilla: int) -> list[str]:
    todos = sorted(s for _, _, ss in GRAFO.ficheros for s in ss)
    return random.Random(semilla).sample(todos, n)


def _consultas(simbolo: str, i: int) -> dict[str, BaseModel]:
    """Una consulta por operación del contrato, a partir de un símbolo semilla."""
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


# ---------------------------------------------------------------------------
# Cliente real, doble y contrato publicado
# ---------------------------------------------------------------------------
def test_cliente_real_y_doble_cumplen_el_protocolo(sembrado: None) -> None:
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        assert isinstance(ConsultaGrafoPostgres(loader), ConsultaGrafo)
    assert isinstance(_doble(GRAFO, []), ConsultaGrafo)


@pytest.mark.parametrize(
    ("tenant", "grafo", "incidentes"),
    [(TENANT, GRAFO, INCIDENTES), (TENANT_OTRO, GRAFO_OTRO, INCIDENTES_OTRO)],
    ids=["tenant", "otro-tenant-mismos-ids"],
)
def test_doble_y_postgres_responden_lo_mismo_y_validan_el_contrato(
    sembrado: None, tenant: str, grafo: GrafoEnMemoria, incidentes: list[IncidenteDoble]
) -> None:
    doble = _doble(grafo, incidentes)
    with GraphLoader(dsn=APP_DSN, tenant_id=tenant) as loader:
        real = ConsultaGrafoPostgres(loader)
        for i, simbolo in enumerate(_simbolos(30, 5)):
            for nombre, consulta in _consultas(simbolo, i).items():
                op = CONTRATO["operaciones"][nombre]
                jsonschema.validate(consulta.model_dump(mode="json"), op["entrada"])
                obtenido = getattr(real, nombre)(consulta)
                jsonschema.validate(_dump(obtenido), op["salida"])
                esperado = getattr(doble, nombre)(consulta)
                # Igualdad de modelos (no de JSON): un datetime se compara por instante, sea cual
                # sea la zona horaria de la sesión de PostgreSQL.
                assert obtenido == esperado, f"{nombre} desde {simbolo}"


# ---------------------------------------------------------------------------
# Presupuesto de latencia por operación (fuente: el contrato)
# ---------------------------------------------------------------------------
OPERACIONES_MEDIDAS = sorted(
    o.nombre
    for o in cm_grafo.OPERACIONES
    if o.verificado_por.endswith("::test_latencia_p95_por_operacion")
)


@pytest.mark.parametrize("operacion", OPERACIONES_MEDIDAS)
def test_latencia_p95_por_operacion(sembrado: None, operacion: str) -> None:
    presupuesto = cm_grafo.presupuesto_p95_ms(operacion)
    consultas = [_consultas(s, i)[operacion] for i, s in enumerate(_simbolos(100, 9))]
    if operacion == "subgrafo":  # el presupuesto se declara a k = 3
        consultas = [ConsultaGrafoIn(nodos=c.nodos, k=3) for c in consultas]  # type: ignore[attr-defined]
    tiempos: list[float] = []
    with GraphLoader(dsn=APP_DSN, tenant_id=TENANT) as loader:
        real = ConsultaGrafoPostgres(loader)
        metodo = getattr(real, operacion)
        for c in consultas[:10]:  # calentamiento: caché de planes y de páginas
            metodo(c)
        for c in consultas:
            inicio = time.perf_counter()
            metodo(c)
            tiempos.append(time.perf_counter() - inicio)
    p50 = statistics.median(tiempos) * 1000
    p95 = statistics.quantiles(tiempos, n=20, method="inclusive")[18] * 1000
    print(f"{operacion}: p50={p50:.1f} ms p95={p95:.1f} ms (presupuesto {presupuesto} ms)")
    assert p95 < presupuesto, f"{operacion}: p95 = {p95:.1f} ms supera {presupuesto} ms"
