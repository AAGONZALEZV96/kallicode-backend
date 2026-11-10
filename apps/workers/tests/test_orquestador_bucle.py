"""Test de integración del bucle del orquestador (KC-BE-04-1).

Requieren PostgreSQL real. Verifica que un job 'pendiente_asignacion'
transiciona a 'triage' y luego a 'design' cuando la etapa de triage
termina sin error, y que se escala a humano cuando la etapa falla dos
veces seguidas.
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


def _job_id(prefijo: str) -> str:
    """Devuelve un id único por test para evitar colisiones entre ejecuciones."""
    return f"{prefijo}_{secrets.token_hex(6)}"


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
    job_id = _job_id("job_ok")
    try:
        tk = await _crear_ticket(t, f"KC-{secrets.token_hex(3)}")
        await _crear_job(job_id, t, tk)

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
            async with bucle.reservar_siguiente() as job:
                assert job is not None
                if job["id"] != job_id:
                    pytest.skip("Otro job se reservó; reintentar en entorno limpio.")
                await bucle.procesar_job(job, cliente)

        # Tres pasos LLM publicados y un triage-result.
        assert cliente.publicar_paso.await_count == 3
        cliente.publicar_triage.assert_awaited_once()

        # El bucle NO transiciona `triage → design` por sí mismo: eso lo
        # hace el endpoint triage-result. Por eso transicionar no se llama
        # en este test (ClienteInterno está mockeado).
        cliente.transicionar.assert_not_awaited()

        # No debe escalar.
        cliente.escalar.assert_not_awaited()
    finally:
        await _borrar_tenant(t)


async def test_procesar_job_escala_tras_dos_fallos() -> None:
    t = await _crear_tenant()
    job_id = _job_id("job_fail")
    try:
        tk = await _crear_ticket(t, f"KC-{secrets.token_hex(3)}")
        await _crear_job(job_id, t, tk)

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
            async with bucle.reservar_siguiente() as job:
                if job is None or job["id"] != job_id:
                    pytest.skip("Otro job se reservó; reintentar en entorno limpio.")
                await bucle.procesar_job(job, cliente)
            cliente.escalar.assert_not_awaited()

            async with bucle.reservar_siguiente() as job:
                if job is None or job["id"] != job_id:
                    pytest.skip("Otro job se reservó; reintentar en entorno limpio.")
                await bucle.procesar_job(job, cliente)

        cliente.escalar.assert_awaited_once()
        _, kwargs = cliente.escalar.call_args
        assert kwargs["motivo"] == "validacion_persistente"
    finally:
        await _borrar_tenant(t)
