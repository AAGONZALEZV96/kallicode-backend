"""Tests de reserva de jobs con FOR UPDATE SKIP LOCKED (KC-BE-04-1).

Requieren PostgreSQL real (service container en CI). Se marcan como
integración y se saltan si no hay base de datos disponible.
"""

from __future__ import annotations

import asyncio
import json
import secrets

import pytest
from kallicode_core.db import sesion_sistema
from kallicode_workers.orquestador.reserva import reservar_siguiente
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


async def _crear_tenant() -> str:
    tenant_id = f"t_{secrets.token_hex(6)}"
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, :n)"),
            {"id": tenant_id, "n": f"Tenant {tenant_id}"},
        )
    return tenant_id


async def _borrar_tenant(tenant_id: str) -> None:
    """Borra el tenant y sus dependencias en orden inverso de FK.

    El esquema no tiene ON DELETE CASCADE desde organizations a tickets, así
    que hay que borrar primero los jobs (que dependen de tickets) y después
    los tickets antes de poder borrar la organización.
    """
    async with sesion_sistema() as db:
        await db.execute(text("DELETE FROM core.jobs WHERE tenant_id = :t"), {"t": tenant_id})
        await db.execute(text("DELETE FROM core.tickets WHERE tenant_id = :t"), {"t": tenant_id})
        await db.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": tenant_id})


async def _crear_ticket(tenant_id: str, numero: str) -> str:
    """Crea un ticket mínimo y devuelve su id (distinto del numero)."""
    ticket_id = f"tk_{secrets.token_hex(6)}"
    async with sesion_sistema() as db:
        await db.execute(
            text("""
                INSERT INTO core.tickets (id, tenant_id, numero, tipo, titulo, descripcion)
                VALUES (:id, :t, :n, 'bug', 'Test', 'Test')
            """),
            {"id": ticket_id, "t": tenant_id, "n": numero},
        )
    return ticket_id


async def _crear_job(
    job_id: str,
    tenant_id: str,
    ticket_id: str,
    prioridad: str,
    estado: str = "pendiente_asignacion",
) -> None:
    async with sesion_sistema() as db:
        await db.execute(
            text("""
                INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad, contexto)
                VALUES (:id, :t, :tk, :e, :prio, CAST(:ctx AS jsonb))
            """),
            {
                "id": job_id,
                "t": tenant_id,
                "tk": ticket_id,
                "e": estado,
                "prio": prioridad,
                "ctx": json.dumps({"titulo": "Test"}),
            },
        )


async def test_reserva_por_prioridad() -> None:
    """P1 sale antes que P4 aunque se cree después."""
    t_a = await _crear_tenant()
    t_b = await _crear_tenant()
    try:
        tk_a = await _crear_ticket(t_a, "KC-A-0001")
        tk_b = await _crear_ticket(t_b, "KC-B-0001")
        await _crear_job("job_p4_0001", t_a, tk_a, "P4")
        await _crear_job("job_p1_0002", t_b, tk_b, "P1")

        job = await reservar_siguiente()
        assert job is not None
        assert job["id"] == "job_p1_0002"
    finally:
        await _borrar_tenant(t_a)
        await _borrar_tenant(t_b)


async def test_reserva_ignora_estados_no_reservables() -> None:
    """Un job en 'design' no es reservable por el Sprint 4."""
    t = await _crear_tenant()
    try:
        tk = await _crear_ticket(t, "KC-D-0001")
        await _crear_job("job_design_01", t, tk, "P1", estado="design")
        job = await reservar_siguiente()
        assert job is None or job["id"] != "job_design_01"
    finally:
        await _borrar_tenant(t)


async def test_reserva_concurrente_no_duplica() -> None:
    """Dos corrutinas pidiendo a la vez: solo una obtiene el job."""
    t = await _crear_tenant()
    try:
        tk = await _crear_ticket(t, "KC-C-0001")
        await _crear_job("job_unico_0001", t, tk, "P2")

        async def _intentar() -> dict | None:
            return await reservar_siguiente()

        resultados = await asyncio.gather(_intentar(), _intentar())
        obtenidos = [r for r in resultados if r is not None and r["id"] == "job_unico_0001"]
        assert len(obtenidos) == 1, f"Se obtuvo el job {len(obtenidos)} veces"
    finally:
        await _borrar_tenant(t)
