"""Presupuesto de tokens de la ingesta contra PostgreSQL real (D2 del ADR-018).

La consulta original de `budget.verificar_presupuesto_tenant` usaba una función y dos columnas que no
existen (`core.ciclo_inicio`, `subscriptions.limite_tokens_mensual`, `usage_tokens.total_tokens`) y
ninguna prueba la ejecutaba: todas simulaban la conexión. Estas pruebas la ejecutan con el rol de la
aplicación y RLS activos.

  * El consumo es el del ciclo de aniversario de la suscripción (core.obtener_ciclo_cuota_actual), o el
    del mes natural si el tenant no tiene suscripción.
  * Solo cuenta el tenant de la sesión, aunque haya consumo de otros.
  * La cuota es CM_PRESUPUESTO_TOKENS_TENANT_MES; se lanza BudgetExceededError si la estimación no cabe.
  * `GraphLoader.registrar_consumo_codigo` escribe con origen 'codemapping_codigo' y lo siguiente que
    se verifica lo cuenta.

Las pruebas de consumo corren en una transacción que se revierte siempre.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from kallicode_codemapping.budget import (
    BudgetExceededError,
    consumo_ciclo_tenant,
    estimar_tokens_ingesta,
    verificar_presupuesto_tenant,
)
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn

ADMIN_DSN = normalizar_dsn(
    os.getenv("KC_DATABASE_URL_MIGRATIONS") or "postgresql://postgres:dev@localhost:5432/kallicode"
)
APP_DSN = ADMIN_DSN + ("&" if "?" in ADMIN_DSN else "?") + "options=-c%20role%3Dkallicode_app"

SIMBOLOS = [{"signature": "def f():", "snippet": "return 1"}]


def _uso(cur: psycopg.Cursor[Any], tenant: str, tokens: int, cuando_sql: str) -> None:
    cur.execute(
        f"""INSERT INTO core.usage_tokens
                (tenant_id, etapa, modelo, tier, invocaciones, tokens_in, tokens_out,
                 duracion_ms, origen, creado_en)
            VALUES (%s, 'prueba', 'm', 'flash', 1, %s, %s, 1, 'codemapping_docs', {cuando_sql})""",
        (tenant, tokens - tokens // 2, tokens // 2),
    )


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[Any]]:
    try:
        c = psycopg.connect(ADMIN_DSN, autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL no disponible para pruebas de integración: {exc}")
    try:
        if c.execute("SELECT to_regproc('core.obtener_ciclo_cuota_actual')").fetchone() == (None,):
            pytest.skip("migración 026 (ciclo de aniversario) sin aplicar")
        yield c
    finally:
        c.rollback()
        c.close()


def _tenants(cur: psycopg.Cursor[Any], *, aniversario: bool) -> tuple[str, str]:
    """Dos tenants nuevos; el primero con suscripción vigente si `aniversario`."""
    a, b = (f"org_pt_{uuid.uuid4().hex[:10]}" for _ in range(2))
    for t in (a, b):
        cur.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (t, t))
    if aniversario:
        # Aniversario el día 10 de un mes pasado: el ciclo vigente empieza un día 10.
        cur.execute(
            """INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, inicia_el, renueva_el)
               VALUES (%s, %s, 'starter', DATE '2026-01-10', DATE '2027-01-10')""",
            (f"sub_{uuid.uuid4().hex[:10]}", a),
        )
    return a, b


def _como_app(cur: psycopg.Cursor[Any]) -> None:
    cur.execute("SET LOCAL ROLE kallicode_app")


def test_cuenta_el_ciclo_de_aniversario_y_solo_el_tenant_de_la_sesion(
    conn: psycopg.Connection[Any],
) -> None:
    with conn.cursor() as cur:
        a, b = _tenants(cur, aniversario=True)
        inicio = f"(core.obtener_ciclo_cuota_actual('{a}')::date)"
        _uso(cur, a, 1_000, f"{inicio} + interval '1 hour'")  # dentro del ciclo
        _uso(cur, a, 50_000, f"{inicio} - interval '1 hour'")  # ciclo anterior
        _uso(cur, b, 70_000, "now()")  # otro tenant
        cur.execute("SELECT core.obtener_ciclo_cuota_actual(%s)", (a,))
        ciclo = cur.fetchone()
        _como_app(cur)

    assert ciclo is not None and len(ciclo[0]) == 10 and ciclo[0].endswith("-10")
    assert consumo_ciclo_tenant(conn, a) == 1_000
    assert consumo_ciclo_tenant(conn, b) == 70_000


def test_sin_suscripcion_el_ciclo_es_el_mes_natural(conn: psycopg.Connection[Any]) -> None:
    with conn.cursor() as cur:
        _a, b = _tenants(cur, aniversario=False)
        _uso(cur, b, 300, "date_trunc('month', now()) + interval '1 hour'")
        _uso(cur, b, 9_000, "date_trunc('month', now()) - interval '1 hour'")
        _como_app(cur)

    assert consumo_ciclo_tenant(conn, b) == 300


def test_lanza_si_la_estimacion_no_cabe_en_lo_que_queda(conn: psycopg.Connection[Any]) -> None:
    prompt, completion = estimar_tokens_ingesta(SIMBOLOS)
    estimado = prompt + completion
    with conn.cursor() as cur:
        a, _b = _tenants(cur, aniversario=True)
        _uso(cur, a, 1_000, "now()")
        _como_app(cur)

    res = verificar_presupuesto_tenant(conn, a, SIMBOLOS, cuota_tokens=1_000 + estimado)
    assert res.cuota_disponible == estimado and res.presupuesto_suficiente

    with pytest.raises(BudgetExceededError, match="consumido 1000"):
        verificar_presupuesto_tenant(conn, a, SIMBOLOS, cuota_tokens=1_000 + estimado - 1)


def test_el_consumo_registrado_por_la_ingesta_cuenta_en_la_siguiente() -> None:
    try:
        admin = psycopg.connect(ADMIN_DSN, autocommit=True)
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL no disponible para pruebas de integración: {exc}")
    tenant = f"org_pt_{uuid.uuid4().hex[:10]}"
    try:
        admin.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (tenant, tenant)
        )
        with GraphLoader(dsn=APP_DSN, tenant_id=tenant) as loader:
            assert loader.registrar_consumo_codigo(None, []) == 0
            assert loader.registrar_consumo_codigo(None, [("m", "flash", 0, 5, 5, 1)]) == 0
            escritas = loader.registrar_consumo_codigo(
                None, [("modelo-a", "flash", 3, 700, 300, 90), ("modelo-b", "pro", 1, 40, 60, 10)]
            )
            assert escritas == 2
            assert consumo_ciclo_tenant(loader.conn, tenant) == 1_100

        filas = admin.execute(
            """SELECT origen, etapa, modelo, tier, invocaciones, tokens_in, tokens_out
                 FROM core.usage_tokens WHERE tenant_id = %s ORDER BY modelo""",
            (tenant,),
        ).fetchall()
        assert filas == [
            ("codemapping_codigo", "ingesta_codigo", "modelo-a", "flash", 3, 700, 300),
            ("codemapping_codigo", "ingesta_codigo", "modelo-b", "pro", 1, 40, 60),
        ]
    finally:
        admin.execute("DELETE FROM core.usage_tokens WHERE tenant_id = %s", (tenant,))
        admin.execute("DELETE FROM core.organizations WHERE id = %s", (tenant,))
        admin.close()
