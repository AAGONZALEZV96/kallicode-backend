"""Cierre transitivo materializado en cm_queries y refresh (KC-CM-11-2).

Verifica:
1. Equivalencia sobre 500 nodos aleatorios entre la tabla materializada (cm.reachability) y el
   recorrido recursivo en vivo.
2. Fallback automático al recorrido en vivo cuando cm.reachability queda obsoleta, registrando
   la ruta ejecutada en las métricas Prometheus (kc_codemapping_impacto_consultas_total).
3. Refresco incremental de cm.reachability desde refresh.py.
4. Medición comparativa de latencia (previa vs posterior a materializar).
"""

from __future__ import annotations

import os
import random
import statistics
import time
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from kallicode_codemapping.cm_queries import _recorrer, dependencias, impacto
from kallicode_codemapping.config import Settings
from kallicode_codemapping.graph_loader import GraphLoader
from kallicode_codemapping.metricas import (
    IMPACTO_CONSULTAS_TOTAL,
    REACHABILITY_REFRESCO_TOTAL,
)
from kallicode_codemapping.refresh import refrescar_reachability_del_refresco
from kallicode_core.cm_impacto import ConsultaGrafoIn

ADMIN_DSN = os.environ.get(
    "KC_DATABASE_URL_ADMIN",
    "postgresql://kallicode_admin:KalliCode_Dev2026!@127.0.0.1:5432/kallicode_test",
)
APP_DSN = os.environ.get(
    "KC_DATABASE_URL_APP",
    "postgresql://kallicode_app:KalliCode_Dev2026!@127.0.0.1:5432/kallicode_test",
)


def _valor_counter(counter: object, **labels: str) -> float:
    return float(counter.labels(**labels)._value.get())  # type: ignore[attr-defined]


@pytest.fixture()
def grafo_500_nodos() -> Iterator[tuple[str, list[str]]]:
    """Crea un tenant aislado con 500 nodos y ~900 aristas de dependencia y lo limpia al salir."""
    try:
        conn_admin = psycopg.connect(ADMIN_DSN, autocommit=True)
    except psycopg.Error as exc:
        pytest.skip(f"PostgreSQL no disponible para pruebas de integración: {exc}")

    tenant_id = f"org_cm112_{uuid.uuid4().hex[:10]}"
    rng = random.Random(1122026)
    nodos = [f"sym_n{i:03d}" for i in range(500)]
    tipos = ("CALLS", "HAS_COLUMN", "REFERENCES", "USES_TABLE")

    with conn_admin, conn_admin.cursor() as cur:
        cur.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, 'Org KC-CM-11-2')",
            (tenant_id,),
        )
        cur.executemany(
            "INSERT INTO cm.nodes (tenant_id, id, kind) VALUES (%s, %s, 'symbol')",
            [(tenant_id, nid) for nid in nodos],
        )
        aristas: set[tuple[str, str, str]] = set()
        for i in range(499):
            aristas.add((nodos[i], nodos[i + 1], tipos[i % len(tipos)]))
        while len(aristas) < 900:
            src_idx = rng.randrange(0, 480)
            dst_idx = min(499, src_idx + rng.randint(1, 20))
            if src_idx != dst_idx:
                aristas.add((nodos[src_idx], nodos[dst_idx], rng.choice(tipos)))

        cur.executemany(
            """
            INSERT INTO cm.edges (tenant_id, src_kind, src_id, dst_kind, dst_id, tipo)
            VALUES (%s, 'symbol', %s, 'symbol', %s, %s)
            """,
            [(tenant_id, s, d, t) for s, d, t in sorted(aristas)],
        )

    try:
        yield tenant_id, nodos
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as cleanup, cleanup.cursor() as cur:
            cur.execute("DELETE FROM cm.reachability WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM cm.reachability_estado WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM cm.edges WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM cm.nodes WHERE tenant_id = %s", (tenant_id,))
            cur.execute("DELETE FROM core.organizations WHERE id = %s", (tenant_id,))


def test_equivalencia_500_nodos_y_fallback_con_metricas(
    grafo_500_nodos: tuple[str, list[str]],
) -> None:
    tenant_id, nodos = grafo_500_nodos
    cfg = Settings(store_database_url=APP_DSN, tenant_id=tenant_id)
    rng = random.Random(42)
    muestra = rng.sample(nodos, 500)

    with GraphLoader(dsn=APP_DSN, tenant_id=tenant_id) as loader:
        # 1. Antes de materializar, la tabla no está vigente: debe caer al recorrido en vivo.
        prev_rec = _valor_counter(
            IMPACTO_CONSULTAS_TOTAL, operacion="impacto", fuente="recursiva", truncado="false"
        )
        latencias_previas_ms: list[float] = []
        for nid in muestra[:30]:
            t0 = time.perf_counter()
            res = impacto(loader, ConsultaGrafoIn(nodos=[nid], k=3))
            latencias_previas_ms.append((time.perf_counter() - t0) * 1000.0)
            assert not res.truncado

        assert (
            _valor_counter(
                IMPACTO_CONSULTAS_TOTAL, operacion="impacto", fuente="recursiva", truncado="false"
            )
            == prev_rec + 30.0
        )

        # 2. Refresco incremental desde refresh.py deja cm.reachability en estado 'vigente'.
        prev_ref = _valor_counter(REACHABILITY_REFRESCO_TOTAL, estado="vigente")
        estado = refrescar_reachability_del_refresco(loader, cfg)
        assert estado == "vigente"
        assert _valor_counter(REACHABILITY_REFRESCO_TOTAL, estado="vigente") == prev_ref + 1.0

        # 3. Equivalencia exacta sobre los 500 nodos aleatorios (materializada vs recursiva).
        prev_mat = _valor_counter(
            IMPACTO_CONSULTAS_TOTAL, operacion="impacto", fuente="materializada", truncado="false"
        )
        latencias_posteriores_ms: list[float] = []
        for idx, nid in enumerate(muestra):
            direccion = "entrante" if idx % 2 == 0 else "saliente"
            op = "impacto" if direccion == "entrante" else "dependencias"
            consulta = ConsultaGrafoIn(nodos=[nid], k=3)

            t0 = time.perf_counter()
            out_auto = (
                impacto(loader, consulta)
                if direccion == "entrante"
                else dependencias(loader, consulta)
            )
            latencias_posteriores_ms.append((time.perf_counter() - t0) * 1000.0)

            out_rec = _recorrer(loader, consulta, op, direccion, ruta="recursiva")
            assert out_auto.nodos == out_rec.nodos
            assert out_auto.semillas == out_rec.semillas
            assert out_auto.truncado == out_rec.truncado

        assert (
            _valor_counter(
                IMPACTO_CONSULTAS_TOTAL,
                operacion="impacto",
                fuente="materializada",
                truncado="false",
            )
            == prev_mat + 250.0
        )

        p95_previo = statistics.quantiles(latencias_previas_ms, n=20)[18]
        p95_posterior = statistics.quantiles(latencias_posteriores_ms, n=20)[18]
        assert p95_posterior < 150.0, (
            f"p95={p95_posterior:.2f}ms excede 150ms (prev={p95_previo:.2f}ms)"
        )

    # 4. Mutación externa en cm.edges deja cm.reachability 'obsoleta' -> fallback automático + métrica.
    with psycopg.connect(ADMIN_DSN, autocommit=True) as conn_admin, conn_admin.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cm.edges (tenant_id, src_kind, src_id, dst_kind, dst_id, tipo)
            VALUES (%s, 'symbol', 'sym_n499', 'symbol', 'sym_n000', 'CALLS')
            """,
            (tenant_id,),
        )

    with GraphLoader(dsn=APP_DSN, tenant_id=tenant_id) as loader:
        antes_fallback = _valor_counter(
            IMPACTO_CONSULTAS_TOTAL, operacion="impacto", fuente="recursiva", truncado="false"
        )
        res_obsoleta = impacto(loader, ConsultaGrafoIn(nodos=["sym_n000"], k=2))
        despues_fallback = _valor_counter(
            IMPACTO_CONSULTAS_TOTAL, operacion="impacto", fuente="recursiva", truncado="false"
        )
        assert despues_fallback == antes_fallback + 1.0
        assert any(n.id == "sym_n499" and n.distancia == 1 for n in res_obsoleta.nodos)
