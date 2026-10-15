"""Test de integración del bucle del orquestador (KC-BE-04-1).

Requieren PostgreSQL real. Verifica que un job 'pendiente_asignacion'
transiciona a 'triage' (por la reserva) y luego a 'design' cuando la etapa
de triage termina sin error, y que se escala a humano cuando la etapa falla
dos veces seguidas.
"""

from __future__ import annotations

import json
import secrets
from unittest.mock import AsyncMock, patch

import pytest
from kallicode_core.db import sesion_sistema
from kallicode_core.llm import ResultadoPaso
from kallicode_core.llm.tiers import Tier
from kallicode_workers.orquestador import bucle
from sqlalchemy import text

pytestmark = [pytest.mark.asyncio, pytest.mark.integracion]


def _resultado(paso_id: str, salida: dict) -> ResultadoPaso:
    return ResultadoPaso(
        paso_id=paso_id,
        version="hash12chars",
        tier=Tier.FLASH,
        modelo="deepseek-v4-flash",
        salida=salida,
        confianza=0.9,
        validacion="esquema_valido",
        tokens_in=10,
        tokens_out=20,
        duracion_ms=100,
        intentos=[],
    )


async def _crear_tenant() -> str:
    tenant_id = f"t_{secrets.token_hex(6)}"
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, :n)"),
            {"id": tenant_id, "n": f"Tenant {tenant_id}"},
        )
    return tenant_id


async def _borrar_tenant(tenant_id: str) -> None:
    """Borra el tenant y sus dependencias en orden inverso de FK."""
    async with sesion_sistema() as db:
        await db.execute(text("DELETE FROM core.jobs WHERE tenant_id = :t"), {"t": tenant_id})
        await db.execute(text("DELETE FROM core.tickets WHERE tenant_id = :t"), {"t": tenant_id})
        await db.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": tenant_id})


async def _crear_ticket(tenant_id: str, numero: str) -> str:
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


async def _crear_job(job_id: str, tenant_id: str, ticket_id: str) -> None:
    async with sesion_sistema() as db:
        await db.execute(
            text("""
                INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad, contexto)
                VALUES (:id, :t, :tk, 'pendiente_asignacion', 'P2', CAST(:ctx AS jsonb))
            """),
            {
                "id": job_id,
                "t": tenant_id,
                "tk": ticket_id,
                "ctx": json.dumps({"titulo": "Algo se rompe", "descripcion": "Test"}),
            },
        )


async def test_procesar_job_avanza_pendiente_a_design() -> None:
    t = await _crear_tenant()
    try:
        tk = await _crear_ticket(t, "KC-0001")
        await _crear_job("job_ok_0001", t, tk)

        cliente = AsyncMock()
        cliente.transicionar = AsyncMock(return_value={})
        cliente.publicar_paso = AsyncMock(return_value={"step_id": "st1"})
        cliente.publicar_triage = AsyncMock(return_value={"ticket_id": "KC-0001"})
        cliente.escalar = AsyncMock()

        salidas = {
            "triage.classify": {
                "type": "bug",
                "summary_en": "Something breaks",
                "symptoms": [],
                "confidence": 0.9,
            },
            "triage.dedup": {"duplicate_of": None, "related_to": []},
            "triage.impact": {
                "affected_modules": [],
                "impact_radius": "low",
                "priority": "P2",
                "estimated_complexity": "small",
                "recommended_tier_downstream": "flash",
            },
        }

        async def fake_ejecutar(paso_id, entrada, tier_minimo=None):
            return _resultado(paso_id, salidas[paso_id])

        with patch(
            "kallicode_workers.orquestador.etapas.ejecutar_paso",
            side_effect=fake_ejecutar,
        ):
            job = await bucle.reservar_siguiente()
            assert job is not None
            if job["id"] != "job_ok_0001":
                pytest.skip("Otro job se reservó; reintentar en entorno limpio.")
            await bucle.procesar_job(job, cliente)

        # Tres pasos LLM publicados + un triage-result
        assert cliente.publicar_paso.await_count == 3
        cliente.publicar_triage.assert_awaited_once()

        # La reserva ya transicionó a 'triage'; el orquestador solo hace
        # una transición adicional: triage → design.
        assert cliente.transicionar.await_count == 1
        llamadas = cliente.transicionar.await_args_list
        assert llamadas[0].kwargs["a"] == "design"

        cliente.escalar.assert_not_awaited()
    finally:
        await _borrar_tenant(t)


async def test_procesar_job_escala_tras_dos_fallos() -> None:
    t = await _crear_tenant()
    try:
        tk = await _crear_ticket(t, "KC-0002")
        await _crear_job("job_fail_0001", t, tk)

        cliente = AsyncMock()
        cliente.transicionar = AsyncMock(return_value={})
        cliente.publicar_paso = AsyncMock()
        cliente.publicar_triage = AsyncMock()
        cliente.escalar = AsyncMock(return_value={"escalation_id": "esc1"})

        async def falla(*args, **kwargs):
            raise RuntimeError("boom")

        with patch(
            "kallicode_workers.orquestador.etapas.ejecutar_paso",
            side_effect=falla,
        ):
            job = await bucle.reservar_siguiente()
            if job is None or job["id"] != "job_fail_0001":
                pytest.skip("Otro job se reservó; reintentar en entorno limpio.")
            await bucle.procesar_job(job, cliente)
            cliente.escalar.assert_not_awaited()

            # Simular un intento previo registrado en iteraciones
            async with sesion_sistema() as db:
                await db.execute(
                    text("UPDATE core.jobs SET iteraciones = CAST(:it AS jsonb) WHERE id = :j"),
                    {"it": json.dumps({"triage": 1}), "j": "job_fail_0001"},
                )

            # La reserva ya lo dejó en 'triage', no en 'pendiente_asignacion';
            # por eso lo forzamos para volver a reservarlo.
            async with sesion_sistema() as db:
                await db.execute(
                    text("UPDATE core.jobs SET estado = 'pendiente_asignacion' WHERE id = :j"),
                    {"j": "job_fail_0001"},
                )

            job = await bucle.reservar_siguiente()
            if job is None or job["id"] != "job_fail_0001":
                pytest.skip("Otro job se reservó; reintentar en entorno limpio.")
            await bucle.procesar_job(job, cliente)

        cliente.escalar.assert_awaited_once()
        _, kwargs = cliente.escalar.call_args
        assert kwargs["motivo"] == "validacion_persistente"
    finally:
        await _borrar_tenant(t)
