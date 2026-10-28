import json
import uuid

import pytest
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema
from kallicode_core.seguridad import emitir_svc_token
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _cab(tenant: str) -> dict:
    return {
        "Authorization": f"Bearer {emitir_svc_token('orquestador', tenant)}",
        "Idempotency-Key": uuid.uuid4().hex,
    }


def _paso(tier: str) -> dict:
    return {
        "etapa": "triage",
        "paso": "clasificar",
        "version_plantilla": "v1",
        "modelo": "claude-fable-5",
        "tier": tier,
        "validacion": "esquema_valido",
        "tokens_in": 10,
        "tokens_out": 5,
        "duracion_ms": 100,
        "entrada_ref": "in",
        "salida_ref": "out",
    }


async def test_tier_vision_recibe_422(client, seed_dos_tenants):
    tenant, _ = seed_dos_tenants
    r = await client.post(
        "/internal/v1/jobs/job_x/steps", json=_paso("vision"), headers=_cab(tenant)
    )
    assert r.status_code == 422, r.text


async def _crear_job(tenant: str, ticket_id: str, job_id: str) -> None:
    async with sesion_sistema() as db:
        await db.execute(
            text(
                """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo,
                                             prioridad, etapa, creado_en)
                   VALUES (:id, :t, 'Ticket tier', 'x', 'bug', 'media', 'triage', now())"""
            ),
            {"id": ticket_id, "t": tenant},
        )
        await db.execute(
            text(
                """INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad)
                   VALUES (:j, :t, :tk, 'triage', 'P3')"""
            ),
            {"j": job_id, "t": tenant, "tk": ticket_id},
        )
        await db.commit()


@pytest.mark.xfail(
    not get_settings().persistir_tier_fable,
    reason="KC-BE-03-3: pendiente de aplicar el CHECK de tier en staging",
    raises=AssertionError,
    strict=False,
)
async def test_fable_se_persiste_tal_cual(client, seed_dos_tenants, monkeypatch):
    monkeypatch.setattr(get_settings(), "persistir_tier_fable", True)
    tenant, _ = seed_dos_tenants
    ticket_id, job_id = f"tk_{uuid.uuid4().hex[:8]}", f"job_{uuid.uuid4().hex[:8]}"
    await _crear_job(tenant, ticket_id, job_id)

    r = await client.post(
        f"/internal/v1/jobs/{job_id}/steps", json=_paso("fable"), headers=_cab(tenant)
    )
    assert r.status_code in (200, 201), r.text
    cuerpo = r.json()
    if "resultado" in cuerpo:  # envoltura del decorador de idempotencia
        cuerpo = json.loads(cuerpo["resultado"]["body"])
    step_id = cuerpo["step_id"]

    async with sesion_sistema() as db:
        paso = (
            await db.execute(text("SELECT tier FROM core.llm_steps WHERE id=:i"), {"i": step_id})
        ).scalar_one()
        uso = (
            await db.execute(
                text("SELECT tier FROM core.usage_tokens WHERE ticket_id=:t"), {"t": ticket_id}
            )
        ).scalar_one()
    assert paso == "fable"
    assert uso == "fable"

    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id="u-owner", tenant_id=tenant, rol="owner", restringido=False
    )
    audit = await client.get(f"/api/v1/audit/steps/{step_id}")
    assert audit.status_code == 200, audit.text
    assert audit.json()["paso"]["tier"] == "fable"
