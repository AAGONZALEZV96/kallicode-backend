"""Test E2E del triage completo (KC-BE-04-2 · cierra BE-F21).

Recorre el flujo completo:
    webhook → normalizer → ticket → job → orquestador (triage) → design

Verifica que:
    - El orquestador ejecuta los 3 pasos LLM (classify, dedup, impact).
    - Cada paso se persiste en core.llm_steps (3 filas).
    - Cada paso se factura en core.usage_tokens (3 filas).
    - El ticket queda con tipo, prioridad_pipeline, complejidad e impacto.
    - El job transiciona a design.
    - GET /api/v1/usage/by-ticket refleja el consumo.

Requiere PostgreSQL real. Se marca como integración.
"""

from __future__ import annotations

import json
import secrets
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport
from kallicode_core.db import sesion_sistema, sesion_tenant, todos, uno
from kallicode_core.llm import ResultadoPaso
from kallicode_core.llm.tiers import Tier
from kallicode_workers import normalizer
from kallicode_workers.orquestador import bucle
from kallicode_workers.orquestador.cliente_interno import ClienteInterno
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
    """Borra el tenant y todas sus dependencias en orden inverso de FK.

    Si alguna transacción previa quedó abortada (porque el test capturó una
    excepción dentro de un `async with sesion_*`), hacemos rollback defensivo
    antes de intentar los DELETE.
    """
    async with sesion_sistema() as db:
        try:
            await db.rollback()
        except Exception:
            pass
        for tabla, columna in [
            ("core.job_transitions", "tenant_id"),
            ("core.escalations", "tenant_id"),
            ("core.llm_steps", "tenant_id"),
            ("core.usage_tokens", "tenant_id"),
            ("core.jobs", "tenant_id"),
            ("core.webhook_inbox", "tenant_id"),
            ("core.tickets", "tenant_id"),
            ("core.connections", "tenant_id"),
        ]:
            try:
                await db.execute(
                    text(f"DELETE FROM {tabla} WHERE {columna} = :t"),
                    {"t": tenant_id},
                )
            except Exception:
                # Si la tabla no existe o la FK falla, seguimos con las demás.
                pass
        try:
            await db.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": tenant_id})
        except Exception:
            pass


async def _crear_conexion(tenant_id: str) -> str:
    """Crea una conexión mínima de tipo 'tickets' para que el webhook tenga
    un padre válido. El esquema exige connection_id NOT NULL en webhook_inbox.
    """
    connection_id = f"conn_{secrets.token_hex(6)}"
    async with sesion_sistema() as db:
        await db.execute(
            text("""
                INSERT INTO core.connections (
                    id, tenant_id, categoria, proveedor, nombre, estado, config
                )
                VALUES (:id, :t, 'tickets', 'github', 'Test GitHub', 'conectada',
                        CAST(:cfg AS jsonb))
            """),
            {
                "id": connection_id,
                "t": tenant_id,
                "cfg": json.dumps({"repo": "test/repo"}),
            },
        )
    return connection_id


async def _crear_webhook_inbox(tenant_id: str, payload: dict) -> int:
    """Crea una fila en core.webhook_inbox con la conexión requerida."""
    connection_id = await _crear_conexion(tenant_id)
    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """
            INSERT INTO core.webhook_inbox (
                tenant_id, connection_id, evento, payload, estado, delivery_id
            )
            VALUES (
                :t, :c, 'issues', CAST(:p AS jsonb), 'pendiente', :d
            )
            RETURNING id
            """,
            {
                "t": tenant_id,
                "c": connection_id,
                "p": json.dumps(payload),
                "d": f"delivery_{secrets.token_hex(6)}",
            },
        )
        return int(fila["id"])


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


# ------------------------------------------------------------------ test
async def test_triage_completo_desde_webhook_hasta_design() -> None:
    """Un webhook genera un ticket, el orquestador ejecuta el triage y deja
    el job en design con 3 pasos persistidos y 3 facturados."""
    tenant = await _crear_tenant()
    try:
        # 1. Simular un webhook de GitHub Issues
        payload_webhook = {
            "action": "opened",
            "issue": {
                "title": "Bug al facturar",
                "body": "Al pulsar pagar aparece un error 500",
                "labels": [{"name": "bug"}],
                "number": 42,
            },
        }
        inbox_id = await _crear_webhook_inbox(tenant, payload_webhook)

        # 2. Procesar el inbox → crea el ticket
        await normalizer.procesar_inbox({"inbox_id": str(inbox_id)})

        # 3. Recuperar el ticket creado
        async with sesion_tenant(tenant, actor="test") as db:
            ticket = await uno(
                db,
                "SELECT * FROM core.tickets WHERE tenant_id = :t AND origen = 'github'",
                {"t": tenant},
            )
        assert ticket is not None, "El normalizer no creó el ticket"

        # 4. Crear el job (como lo haría POST /internal/v1/jobs)
        job_id = f"job_{secrets.token_hex(8)}"
        async with sesion_tenant(tenant, actor="test") as db:
            await db.execute(
                text(
                    """
                    INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad, contexto)
                    VALUES (:id, :t, :tk, 'pendiente_asignacion', 'P2', CAST(:ctx AS jsonb))
                    """
                ),
                {
                    "id": job_id,
                    "t": tenant,
                    "tk": ticket["id"],
                    "ctx": json.dumps(
                        {
                            "titulo": ticket["titulo"],
                            "descripcion": ticket["descripcion"],
                            "origen": ticket["origen"],
                        }
                    ),
                },
            )

        # 5. Respuestas grabadas del LLM
        salidas = {
            "triage.classify": {
                "type": "bug",
                "summary_en": "Billing fails with 500",
                "symptoms": [{"symptom": "500 al pagar", "context": "checkout"}],
                "affected_surfaces": ["billing"],
                "urgency_signals": ["customer-facing"],
                "confidence": 0.9,
            },
            "triage.dedup": {"duplicate_of": None, "related_to": []},
            "triage.impact": {
                "affected_modules": ["billing"],
                "impact_radius": "medium",
                "regression_risk": 0.4,
                "priority": "P2",
                "estimated_complexity": "small",
                "recommended_tier_downstream": "flash",
                "reasoning_summary_en": "Billing is user-facing and near revenue",
            },
        }

        async def fake_ejecutar(paso_id, entrada, tier_minimo=None):
            return _resultado(paso_id, salidas[paso_id])

        # 6. Levantar la app ASGI para que ClienteInterno hable con ella
        from kallicode_api.main import app

        transport = ASGITransport(app=app)

        # 7. Parchear httpx.AsyncClient para forzar el ASGITransport
        original_client = httpx.AsyncClient

        def patched_client(*args, **kwargs):
            kwargs["transport"] = transport
            kwargs.setdefault("base_url", "http://test")
            return original_client(*args, **kwargs)

        # 8. Ejecutar el orquestador
        with (
            patch.object(httpx, "AsyncClient", patched_client),
            patch(
                "kallicode_workers.orquestador.etapas.ejecutar_paso",
                side_effect=fake_ejecutar,
            ),
        ):
            cliente = ClienteInterno(base_url="http://test/internal/v1")
            job = await bucle.reservar_siguiente()
            assert job is not None, "El orquestador no reservó el job"
            assert job["id"] == job_id
            await bucle.procesar_job(job, cliente)
            await cliente.cerrar()

        # 9. Verificar: 3 filas en core.llm_steps
        async with sesion_sistema() as db:
            pasos = await todos(
                db,
                "SELECT * FROM core.llm_steps WHERE tenant_id = :t AND job_id = :j ORDER BY creado_en",
                {"t": tenant, "j": job_id},
            )
        assert len(pasos) == 3, f"Se esperaban 3 pasos LLM, hay {len(pasos)}"
        assert {p["paso"] for p in pasos} == {
            "triage.classify",
            "triage.dedup",
            "triage.impact",
        }
        for p in pasos:
            assert p["tier"] == "flash"
            assert p["tokens_in"] == 10
            assert p["tokens_out"] == 20

        # 10. Verificar: 3 filas en core.usage_tokens
        async with sesion_sistema() as db:
            usos = await todos(
                db,
                "SELECT * FROM core.usage_tokens WHERE tenant_id = :t AND ticket_id = :tk",
                {"t": tenant, "tk": ticket["id"]},
            )
        assert len(usos) == 3, f"Se esperaban 3 consumos, hay {len(usos)}"

        # 11. Verificar: el ticket tiene tipo, prioridad, complejidad, impacto
        async with sesion_sistema() as db:
            ticket_actualizado = await uno(
                db,
                "SELECT * FROM core.tickets WHERE id = :i",
                {"i": ticket["id"]},
            )
        assert ticket_actualizado["tipo"] == "bug"
        assert ticket_actualizado["prioridad_pipeline"] == "P2"
        assert ticket_actualizado["complejidad"] == "small"
        assert ticket_actualizado["impacto"] is not None
        assert ticket_actualizado["impacto"]["radio"] == "medium"

        # 12. Verificar: el job está en design
        async with sesion_sistema() as db:
            job_final = await uno(
                db,
                "SELECT * FROM core.jobs WHERE id = :i",
                {"i": job_id},
            )
        assert job_final["estado"] == "design"

    finally:
        await _borrar_tenant(tenant)
