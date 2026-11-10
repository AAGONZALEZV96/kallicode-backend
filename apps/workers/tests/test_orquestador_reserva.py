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


def _job_id(prefijo: str) -> str:
    """Devuelve un id único por test para evitar colisiones entre ejecuciones."""
    return f"{prefijo}_{secrets.token_hex(6)}"


async def _crear_tenant() -> str:
    tenant_id = f"t_{secrets.token_hex(6)}"
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, :n)"),
            {"id": tenant_id, "n": f"Tenant {tenant_id}"},
        )
    return tenant_id


async def _borrar_tenant(tenant_id: str) -> None:
    """Borra el tenant; los artefactos probatorios se eliminan por cascada del job."""
    async with sesion_sistema() as db:
        for tabla in (
            "core.escalations",
            "core.jobs",
            "core.webhook_inbox",
            "core.tickets",
            "core.connections",
            "core.ticket_counters",
        ):
            await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id = :t"), {"t": tenant_id})
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
    job_p4 = _job_id("job_p4")
    job_p1 = _job_id("job_p1")
    try:
        tk_a = await _crear_ticket(t_a, f"KC-A-{secrets.token_hex(3)}")
        tk_b = await _crear_ticket(t_b, f"KC-B-{secrets.token_hex(3)}")
        await _crear_job(job_p4, t_a, tk_a, "P4")
        await _crear_job(job_p1, t_b, tk_b, "P1")

        async with reservar_siguiente() as job:
            assert job is not None
            assert job["id"] == job_p1, f"Se esperaba {job_p1}, se obtuvo {job['id']}"
    finally:
        await _borrar_tenant(t_a)
        await _borrar_tenant(t_b)


async def test_reserva_ignora_estados_que_esperan_firma() -> None:
    """Un job en gate1 espera aprobación y no vuelve a ejecutarse."""
    t = await _crear_tenant()
    job_gate = _job_id("job_gate")
    try:
        tk = await _crear_ticket(t, f"KC-D-{secrets.token_hex(3)}")
        await _crear_job(job_gate, t, tk, "P1", estado="gate1")
        async with reservar_siguiente() as job:
            assert job is None or job["id"] != job_gate
    finally:
        await _borrar_tenant(t)


async def test_reserva_etapas_design_y_build() -> None:
    """Las etapas posteriores a triage también vuelven a entrar al orquestador."""
    t = await _crear_tenant()
    job_design = _job_id("job_design")
    job_build = _job_id("job_build")
    try:
        tk_design = await _crear_ticket(t, f"KC-D-{secrets.token_hex(3)}")
        tk_build = await _crear_ticket(t, f"KC-B-{secrets.token_hex(3)}")
        await _crear_job(job_design, t, tk_design, "P1", estado="design")
        await _crear_job(job_build, t, tk_build, "P1", estado="build")

        reservados = set()
        for _ in range(2):
            async with reservar_siguiente() as job:
                if job is not None:
                    reservados.add((job["id"], job["estado"]))
                    if job["id"] in {job_design, job_build}:
                        async with sesion_sistema() as db:
                            await db.execute(
                                text("UPDATE core.jobs SET estado = 'gate1' WHERE id = :id"),
                                {"id": job["id"]},
                            )

        assert (job_design, "design") in reservados
        assert (job_build, "build") in reservados
    finally:
        await _borrar_tenant(t)


async def test_reserva_concurrente_no_duplica() -> None:
    """Dos corrutinas pidiendo a la vez: solo una obtiene el job."""
    t = await _crear_tenant()
    job_unico = _job_id("job_unico")
    try:
        tk = await _crear_ticket(t, f"KC-C-{secrets.token_hex(3)}")
        await _crear_job(job_unico, t, tk, "P2")

        reserva_adquirida = asyncio.Event()
        liberar_reserva = asyncio.Event()

        async def _mantener_reserva() -> dict | None:
            async with reservar_siguiente() as job:
                reserva_adquirida.set()
                await liberar_reserva.wait()
                return job

        primera = asyncio.create_task(_mantener_reserva())
        await reserva_adquirida.wait()
        try:
            async with reservar_siguiente() as segunda:
                assert segunda is None or segunda["id"] != job_unico
        finally:
            liberar_reserva.set()

        primera_reserva = await primera
        assert primera_reserva is not None
        assert primera_reserva["id"] == job_unico
    finally:
        await _borrar_tenant(t)
