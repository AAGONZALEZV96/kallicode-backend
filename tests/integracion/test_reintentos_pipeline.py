"""Tests de integración de las reglas de reintento y cuarentena (KC-BE-06-4).

Escenarios:
  - Un criterio que falla 2 veces → escalación con `validacion_persistente`
    y el job queda en `escalado_humano`.
  - Un criterio que falla y pasa en el reintento → marca flaky y el pipeline
    sigue a gate2 sin escalación.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from kallicode_core.db import sesion_sistema
from kallicode_core.seguridad import emitir_svc_token
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _cabeceras(tenant_id: str, key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {emitir_svc_token('orquestador', tenant_id)}",
        "Idempotency-Key": key,
    }


async def _crear_ticket(tenant_id: str) -> dict[str, Any]:
    ticket_id = str(uuid.uuid4())
    async with sesion_sistema() as db:
        fila = await db.execute(
            text(
                """INSERT INTO core.tickets
                   (id, tenant_id, titulo, descripcion, tipo, prioridad, etapa, creado_en)
                   VALUES (:id, :t, 'Ticket', 'Desc', 'bug', 'media', 'qa', now())
                   RETURNING id, numero, tenant_id"""
            ),
            {"id": ticket_id, "t": tenant_id},
        )
        await db.commit()
        return dict(fila.mappings().one())


async def _preparar_job(
    tenant_id: str,
    ticket: dict[str, Any],
) -> dict[str, Any]:
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    spec_id = f"sp_{uuid.uuid4().hex[:12]}"
    user_id = f"u_{uuid.uuid4().hex[:8]}"

    async with sesion_sistema() as db:
        await db.execute(
            text(
                """INSERT INTO core.users (id, tenant_id, email, nombre, rol)
                   VALUES (:id, :t, :email, 'Test', 'admin')"""
            ),
            {"id": user_id, "t": tenant_id, "email": f"{user_id}@{tenant_id[:8]}.test"},
        )
        await db.execute(
            text(
                """INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad)
                   VALUES (:id, :t, :tk, 'qa', 'P3')"""
            ),
            {"id": job_id, "t": tenant_id, "tk": ticket["id"]},
        )
        await db.execute(
            text(
                """INSERT INTO core.specs
                   (id, tenant_id, ticket_id, version, spec_md_ref, scope_paths,
                    migraciones, estado)
                   VALUES (:id, :t, :tk, 1, 'mem://spec.md', :sp,
                           CAST(:mg AS jsonb), 'aprobada')"""
            ),
            {
                "id": spec_id,
                "t": tenant_id,
                "tk": ticket["id"],
                "sp": ["src/"],
                "mg": "[]",
            },
        )
        for cid in ("AC-1", "AC-2"):
            await db.execute(
                text(
                    """INSERT INTO core.spec_criteria
                       (spec_id, tenant_id, criterio_id, given_txt, when_txt, then_txt)
                       VALUES (:s, :t, :c, 'g', 'w', 't')"""
                ),
                {"s": spec_id, "t": tenant_id, "c": cid},
            )
        await db.commit()

    return {"id": job_id, "tenant_id": tenant_id, "ticket_id": ticket["id"]}


async def test_criterio_falla_dos_veces_escala(
    client_interna: AsyncClient,
    seed_dos_tenants,
) -> None:
    tenant_a, _ = seed_dos_tenants
    ticket = await _crear_ticket(tenant_a)
    job = await _preparar_job(tenant_a, ticket)

    matriz_falla = [
        {
            "criterio_id": "AC-1",
            "resultado": "falla",
            "extracto_fallo": "assert x == 1",
            "evidencias": [],
        },
        {
            "criterio_id": "AC-2",
            "resultado": "pasa",
            "extracto_fallo": None,
            "evidencias": [],
        },
    ]

    # Mock de cuota para que no dispare pausado_por_cuota
    with patch(
        "kallicode_api.routers.internal.comercial.cuota_agotada",
        new=AsyncMock(return_value=False),
    ):
        # Corrida 1: AC-1 falla. Debe pedir reintento.
        r1 = await client_interna.post(
            f"/internal/v1/tickets/{ticket['numero']}/evidence",
            json={
                "corrida": 1,
                "matriz": matriz_falla,
                "regresion": {},
                "flaky": [],
                "veredicto_visual": [],
            },
            headers=_cabeceras(tenant_a, "ev-1"),
        )
        assert r1.status_code == 201, r1.text
        assert r1.json()["accion"] == "reintentar"

        # Corrida 2: AC-1 vuelve a fallar. Debe escalar.
        r2 = await client_interna.post(
            f"/internal/v1/tickets/{ticket['numero']}/evidence",
            json={
                "corrida": 2,
                "matriz": matriz_falla,
                "regresion": {},
                "flaky": [],
                "veredicto_visual": [],
            },
            headers=_cabeceras(tenant_a, "ev-2"),
        )
        assert r2.status_code == 201, r2.text
        cuerpo = r2.json()
        assert cuerpo["accion"] == "escalar"
        assert "AC-1" in cuerpo["criterios"]

    async with sesion_sistema() as db:
        estado = await db.execute(
            text("SELECT estado FROM core.jobs WHERE id=:j"), {"j": job["id"]}
        )
        assert estado.scalar() == "escalado_humano"
        esc = await db.execute(
            text(
                """SELECT motivo FROM core.escalations
                   WHERE job_id=:j AND estado='abierta'"""
            ),
            {"j": job["id"]},
        )
        assert esc.scalar() == "validacion_persistente"


async def test_pass_on_retry_marca_flaky(
    client_interna: AsyncClient,
    seed_dos_tenants,
) -> None:
    tenant_a, _ = seed_dos_tenants
    ticket = await _crear_ticket(tenant_a)
    job = await _preparar_job(tenant_a, ticket)

    matriz_flaky = [
        {
            "criterio_id": "AC-1",
            "resultado": "flaky",
            "extracto_fallo": "falló una vez, pasó en reintento",
            "evidencias": [],
        },
        {
            "criterio_id": "AC-2",
            "resultado": "pasa",
            "extracto_fallo": None,
            "evidencias": [],
        },
    ]

    with patch(
        "kallicode_api.routers.internal.comercial.cuota_agotada",
        new=AsyncMock(return_value=False),
    ):
        r = await client_interna.post(
            f"/internal/v1/tickets/{ticket['numero']}/evidence",
            json={
                "corrida": 1,
                "matriz": matriz_flaky,
                "regresion": {},
                "flaky": ["AC-1"],
                "veredicto_visual": [],
            },
            headers=_cabeceras(tenant_a, "ev-flaky"),
        )
        assert r.status_code == 201, r.text
        cuerpo = r.json()
        assert cuerpo["accion"] == "flaky"
        assert cuerpo["siguiente"] == "gate2"
        assert "AC-1" in cuerpo["criterios_flaky"]

    # El job NO se escala y avanza a gate2.
    async with sesion_sistema() as db:
        estado = await db.execute(
            text("SELECT estado FROM core.jobs WHERE id=:j"), {"j": job["id"]}
        )
        assert estado.scalar() == "gate2"
        esc = await db.execute(
            text(
                """SELECT count(*) FROM core.escalations
                   WHERE job_id=:j AND estado='abierta'"""
            ),
            {"j": job["id"]},
        )
        assert esc.scalar() == 0
        # Verificar el evento qa_flaky en auditoría (contrato con Testing).
        ev = await db.execute(
            text(
                """SELECT datos FROM audit.audit_events
                   WHERE tenant_id=:t AND evento='qa_flaky'
                   ORDER BY creado_en DESC LIMIT 1"""
            ),
            {"t": tenant_a},
        )
        datos_evento = ev.scalar()
        assert datos_evento is not None
        assert "AC-1" in str(datos_evento)
