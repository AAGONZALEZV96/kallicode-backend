"""Tests de KC-BE-04-5 (cierra BE-F18).

- Renovar 3 veces con la misma suscripción vencida avanza un solo mes.
- Dos ciclo() concurrentes con el mismo tenant → un solo mes.
- con_lider serializa dos "réplicas": solo una cede True.

Requiere PostgreSQL real. Se marca como integración.
"""

from __future__ import annotations

import asyncio
import secrets

import pytest
from kallicode_core.db import sesion_sistema, uno
from kallicode_workers import billing_cycle
from kallicode_workers.lider import con_lider
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


# --------------------------------------------------------------- helpers
async def _crear_tenant() -> str:
    tenant_id = f"t_{secrets.token_hex(6)}"
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, :n)"),
            {"id": tenant_id, "n": f"Tenant {tenant_id}"},
        )
    return tenant_id


async def _borrar_tenant(tenant_id: str) -> None:
    async with sesion_sistema() as db:
        try:
            await db.rollback()
        except Exception:
            pass
        for tabla in (
            "core.job_transitions",
            "core.escalations",
            "core.llm_steps",
            "core.usage_tokens",
            "core.jobs",
            "core.webhook_inbox",
            "core.usage_cycles",
            "core.subscriptions",
            "core.notifications",
            "core.tickets",
            "core.connections",
        ):
            try:
                await db.execute(
                    text(f"DELETE FROM {tabla} WHERE tenant_id = :t"),
                    {"t": tenant_id},
                )
            except Exception:
                pass
        try:
            await db.execute(
                text("DELETE FROM core.organizations WHERE id = :t"),
                {"t": tenant_id},
            )
        except Exception:
            pass


async def _crear_suscripcion_vencida(tenant_id: str) -> str:
    """Crea una suscripción con renueva_el ayer.

    Requiere un plan existente en core.plans (FK plan_codigo). Si no
    hay ninguno, el test hace skip: el setup de planes es del Equipo 2.
    """
    sub_id = f"sub_{secrets.token_hex(6)}"
    async with sesion_sistema() as db:
        plan = await uno(db, "SELECT codigo FROM core.plans LIMIT 1")
        if plan is None:
            pytest.skip("no hay filas en core.plans; el test necesita un plan")
        plan_codigo = plan["codigo"]

        await db.execute(
            text("""
                INSERT INTO core.subscriptions (
                    id, tenant_id, plan_codigo, estado,
                    inicia_el, renueva_el, finaliza_el
                )
                VALUES (
                    :id, :t, :p, 'activa',
                    current_date - interval '2 month',
                    current_date - interval '1 day',
                    NULL
                )
            """),
            {"id": sub_id, "t": tenant_id, "p": plan_codigo},
        )
    return sub_id


async def _get_renueva_el(sub_id: str):
    async with sesion_sistema() as db:
        row = await uno(
            db,
            "SELECT renueva_el FROM core.subscriptions WHERE id = :id",
            {"id": sub_id},
        )
    return row["renueva_el"]


# ------------------------------------------------------------------ tests
async def test_renovar_tres_veces_avanza_un_mes():
    """La renovación es idempotente por ciclo: renovar 3 veces no avanza 3 meses."""
    tenant = await _crear_tenant()
    try:
        sub_id = await _crear_suscripcion_vencida(tenant)
        antes = await _get_renueva_el(sub_id)

        await billing_cycle.ciclo()
        despues1 = await _get_renueva_el(sub_id)
        assert despues1 > antes, f"no avanzó: {antes} → {despues1}"

        # Segunda y tercera: renueva_el ya NO está vencida → no avanzan.
        await billing_cycle.ciclo()
        await billing_cycle.ciclo()
        despues3 = await _get_renueva_el(sub_id)
        assert despues3 == despues1, f"avanzó más de un mes: {despues1} → {despues3}"
    finally:
        await _borrar_tenant(tenant)


async def test_dos_ciclo_concurrentes_avanzan_un_mes():
    """Dos ciclo() concurrentes: solo una avanza el mes."""
    tenant = await _crear_tenant()
    try:
        sub_id = await _crear_suscripcion_vencida(tenant)
        antes = await _get_renueva_el(sub_id)

        await asyncio.gather(
            billing_cycle.ciclo(),
            billing_cycle.ciclo(),
        )

        despues = await _get_renueva_el(sub_id)
        async with sesion_sistema() as db:
            esperado = await uno(
                db,
                "SELECT CAST(CAST(:a AS date) + interval '1 month' AS date) AS d",
                {"a": antes},
            )
        assert despues == esperado["d"], (
            f"avanzó más de un mes: {antes} → {despues} (esperado {esperado['d']})"
        )
    finally:
        await _borrar_tenant(tenant)


async def test_con_lider_serializa_dos_replicas():
    """Dos con_lider('x') concurrentes: solo una cede True."""
    resultados: list[bool] = []

    async def _replica():
        async with con_lider("test_billing_cycle_lider") as es_lider:
            resultados.append(es_lider)
            # Mantener el lock un instante para que la otra llegue a intentarlo.
            await asyncio.sleep(0.1)

    await asyncio.gather(_replica(), _replica())

    assert sorted(resultados) == [False, True], f"se esperaba [False, True], se obtuvo {resultados}"
