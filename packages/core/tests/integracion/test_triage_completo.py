"""Test E2E del triage completo (KC-BE-04-2 · cierra BE-F21).

Recorre:
    webhook → normalizer → orquestador → design

Comprueba persistencia de pasos LLM, consumo, actualización del ticket
y consulta del consumo mediante la API pública.

Requiere PostgreSQL real.
"""

from __future__ import annotations

import json
import secrets
from unittest.mock import AsyncMock, patch

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

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integracion,
]


async def _crear_tenant() -> str:
    tenant_id = f"t_{secrets.token_hex(6)}"

    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, :n)"),
            {
                "id": tenant_id,
                "n": f"Tenant {tenant_id}",
            },
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


async def _crear_conexion(tenant_id: str) -> str:
    connection_id = f"conn_{secrets.token_hex(6)}"

    async with sesion_sistema() as db:
        await db.execute(
            text(
                """
                INSERT INTO core.connections (
                    id,
                    tenant_id,
                    categoria,
                    proveedor,
                    nombre,
                    estado,
                    config
                )
                VALUES (
                    :id,
                    :t,
                    'tickets',
                    'github',
                    'Test GitHub',
                    'conectada',
                    CAST(:cfg AS jsonb)
                )
                """
            ),
            {
                "id": connection_id,
                "t": tenant_id,
                "cfg": json.dumps({"repo": "test/repo"}),
            },
        )

    return connection_id


async def _crear_webhook_inbox(
    tenant_id: str,
    payload: dict,
) -> int:
    connection_id = await _crear_conexion(tenant_id)

    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """
            INSERT INTO core.webhook_inbox (
                tenant_id,
                connection_id,
                evento,
                payload,
                estado,
                delivery_id
            )
            VALUES (
                :t,
                :c,
                'issues',
                CAST(:p AS jsonb),
                'pendiente',
                :d
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


def _resultado(
    paso_id: str,
    salida: dict,
) -> ResultadoPaso:
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


def _token_para(tenant_id: str) -> str | None:
    """Emite un token de usuario para consultar el consumo público."""
    try:
        from kallicode_core.seguridad import emitir_access_token
    except Exception:
        return None

    try:
        return emitir_access_token(
            user_id=f"u_{secrets.token_hex(4)}",
            tenant_id=tenant_id,
            rol="owner",
        )
    except Exception:
        return None


async def test_triage_completo_desde_webhook_hasta_design() -> None:
    """Webhook → normalizer → orquestador → design."""
    tenant = await _crear_tenant()

    try:
        payload_webhook = {
            "action": "opened",
            "issue": {
                "title": "Bug al facturar",
                "body": "Al pulsar pagar aparece un error 500",
                "labels": [{"name": "bug"}],
                "number": 42,
            },
        }
        inbox_id = await _crear_webhook_inbox(
            tenant,
            payload_webhook,
        )

        with patch(
            "kallicode_workers.normalizer.cuota_agotada",
            new=AsyncMock(return_value=False),
        ):
            await normalizer.procesar_inbox({"inbox_id": str(inbox_id)})

        async with sesion_tenant(
            tenant,
            actor="test",
        ) as db:
            ticket = await uno(
                db,
                """
                SELECT *
                FROM core.tickets
                WHERE tenant_id = :t AND origen = 'github'
                """,
                {"t": tenant},
            )

        assert ticket is not None, "el normalizador no creó el ticket"

        async with sesion_sistema() as db:
            job = await uno(
                db,
                "SELECT * FROM core.jobs WHERE tenant_id = :t",
                {"t": tenant},
            )

        assert job is not None, "el normalizador no creó el job (KC-BE-04-2 incompleto)"
        assert job["ticket_id"] == ticket["id"], (
            f"job.ticket_id={job['ticket_id']} != ticket.id={ticket['id']}"
        )
        assert job["estado"] == "pendiente_asignacion", f"estado inesperado: {job['estado']}"

        job_id = job["id"]

        salidas = {
            "triage.classify": {
                "type": "bug",
                "summary_en": "Billing fails with 500",
                "symptoms": [
                    {
                        "symptom": "500 al pagar",
                        "context": "checkout",
                    }
                ],
                "affected_surfaces": ["billing"],
                "urgency_signals": ["customer-facing"],
                "confidence": 0.9,
            },
            "triage.dedup": {
                "duplicate_of": None,
                "related_to": [],
            },
            "triage.impact": {
                "affected_modules": ["billing"],
                "impact_radius": "medium",
                "regression_risk": 0.4,
                "priority": "P2",
                "estimated_complexity": "small",
                "recommended_tier_downstream": "flash",
                "reasoning_summary_en": ("Billing is user-facing and near revenue"),
            },
        }

        async def fake_ejecutar(
            paso_id,
            entrada,
            tier_minimo=None,
        ):
            return _resultado(
                paso_id,
                salidas[paso_id],
            )

        from kallicode_api.main import app as app_publica
        from kallicode_api.main_interna import app as app_interna

        # El orquestador consume exclusivamente la aplicación interna.
        transport = ASGITransport(app=app_interna)
        original_client = httpx.AsyncClient

        def patched_client(*args, **kwargs):
            kwargs["transport"] = transport
            kwargs.setdefault(
                "base_url",
                "http://test",
            )
            return original_client(*args, **kwargs)

        with (
            patch.object(
                httpx,
                "AsyncClient",
                patched_client,
            ),
            patch(
                "kallicode_workers.orquestador.etapas.ejecutar_paso",
                side_effect=fake_ejecutar,
            ),
        ):
            cliente = ClienteInterno(base_url="http://test/internal/v1")
            reservado = await bucle.reservar_siguiente()

            assert reservado is not None, "el orquestador no reservó el job"
            assert reservado["id"] == job_id

            await bucle.procesar_job(
                reservado,
                cliente,
            )
            await cliente.cerrar()

        async with sesion_sistema() as db:
            pasos = await todos(
                db,
                """
                SELECT *
                FROM core.llm_steps
                WHERE tenant_id = :t AND job_id = :j
                ORDER BY creado_en
                """,
                {
                    "t": tenant,
                    "j": job_id,
                },
            )

        assert len(pasos) == 3, f"se esperaban 3 pasos LLM, hay {len(pasos)}"
        assert {paso["paso"] for paso in pasos} == {
            "triage.classify",
            "triage.dedup",
            "triage.impact",
        }

        for paso in pasos:
            assert paso["tier"] == "flash"
            assert paso["tokens_in"] == 10
            assert paso["tokens_out"] == 20

        async with sesion_sistema() as db:
            usos = await todos(
                db,
                """
                SELECT *
                FROM core.usage_tokens
                WHERE tenant_id = :t AND ticket_id = :tk
                """,
                {
                    "t": tenant,
                    "tk": ticket["id"],
                },
            )

        assert len(usos) == 3, f"se esperaban 3 consumos, hay {len(usos)}"

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

        async with sesion_sistema() as db:
            job_final = await uno(
                db,
                "SELECT * FROM core.jobs WHERE id = :i",
                {"i": job_id},
            )

        assert job_final["estado"] == "design"

        token = _token_para(tenant)

        if token is None:
            pytest.skip(
                "emitir_access_token no disponible: no se pudo verificar GET /usage/by-ticket."
            )

        # La consulta del portal utiliza la aplicación pública.
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app_publica),
            base_url="http://test",
        ) as client:
            respuesta = await client.get(
                "/api/v1/usage/by-ticket",
                headers={
                    "Authorization": f"Bearer {token}",
                },
            )

        assert respuesta.status_code == 200, respuesta.text
        datos = respuesta.json()
        assert "items" in datos, datos

        item = next(
            (fila for fila in datos["items"] if fila["ticket_id"] == ticket["numero"]),
            None,
        )

        assert item is not None, (
            f"ticket {ticket['numero']} no aparece "
            "en /usage/by-ticket: "
            f"{[fila['ticket_id'] for fila in datos['items']]}"
        )

        # 3 pasos × (10 tokens_in + 20 tokens_out).
        assert item["tokens"] == 90, item

    finally:
        await _borrar_tenant(tenant)
