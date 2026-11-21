"""La confianza de los pasos LLM sale como número JSON (KC-PT-08-2).

core.llm_steps.confianza es numeric(4,3): llega como Decimal y, dentro de los dict[str, Any] de
ExpedienteOut y DetallePasoLlmOut, Pydantic la serializaba como texto ("0.870"). El portal valida
un número, así que el expediente de cualquier ticket con un paso con confianza no se mostraba.
"""

import uuid

import pytest
import pytest_asyncio
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.db import sesion_sistema
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _como(tenant: str, rol: str) -> None:
    """Usuario autenticado del tenant con ese rol (el conftest limpia el override al final)."""
    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=f"u-{rol}", tenant_id=tenant, rol=rol, restringido=False
    )


async def _paso(tenant: str, ticket_id: str, job_id: str, confianza: float | None) -> str:
    step_id = f"st_{uuid.uuid4().hex[:12]}"
    async with sesion_sistema() as db:
        await db.execute(
            text(
                """INSERT INTO core.llm_steps (id, tenant_id, job_id, ticket_id, etapa, paso,
                       version_plantilla, modelo, tier, confianza, validacion, tokens_in,
                       tokens_out, duracion_ms, entrada_ref, salida_ref)
                   VALUES (:id, :t, :j, :tk, 'triage', 'triage.classify', '3',
                       'gemini-2.5-flash', 'flash', :c, 'esquema_valido', 10, 5, 100,
                       'mem://entrada', 'mem://salida')"""
            ),
            {"id": step_id, "t": tenant, "j": job_id, "tk": ticket_id, "c": confianza},
        )
        await db.commit()
    return step_id


@pytest_asyncio.fixture
async def ticket_con_pasos(seed_dos_tenants, crear_ticket):
    tenant, _ = seed_dos_tenants
    ticket = await crear_ticket(tenant)
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    async with sesion_sistema() as db:
        ticket_id = (
            await db.execute(
                text("SELECT id FROM core.tickets WHERE tenant_id=:t AND numero=:n"),
                {"t": tenant, "n": ticket["numero"]},
            )
        ).scalar_one()
        await db.execute(
            text(
                """INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad)
                   VALUES (:j, :t, :tk, 'triage', 'P3')"""
            ),
            {"j": job_id, "t": tenant, "tk": ticket_id},
        )
        await db.commit()
    con = await _paso(tenant, ticket_id, job_id, 0.87)
    sin = await _paso(tenant, ticket_id, job_id, None)
    return tenant, ticket["numero"], con, sin


async def test_expediente_entrega_la_confianza_como_numero(client, ticket_con_pasos):
    tenant, numero, con, sin = ticket_con_pasos
    _como(tenant, "owner")

    r = await client.get(f"/api/v1/audit/tickets/{numero}")

    assert r.status_code == 200, r.text
    pasos = {p["step_id"]: p for e in r.json()["etapas"] for p in e["pasos"]}
    assert pasos[con]["confianza"] == 0.87
    assert isinstance(pasos[con]["confianza"], float)
    assert pasos[sin]["confianza"] is None


async def test_detalle_del_paso_entrega_la_confianza_como_numero(client, ticket_con_pasos):
    tenant, _, con, sin = ticket_con_pasos
    _como(tenant, "owner")

    r_con = await client.get(f"/api/v1/audit/steps/{con}")
    r_sin = await client.get(f"/api/v1/audit/steps/{sin}")

    assert r_con.status_code == 200, r_con.text
    assert r_con.json()["ejecucion"]["confianza"] == 0.87
    assert isinstance(r_con.json()["ejecucion"]["confianza"], float)
    assert r_sin.json()["ejecucion"]["confianza"] is None
    # Sin traza en Blob (refs mem://) no hay URLs firmadas: el portal lo dice en el panel.
    assert r_con.json()["entrada_url"] is None
    assert r_con.json()["salida_url"] is None


async def test_el_approver_no_ve_el_detalle_del_paso(client, ticket_con_pasos):
    tenant, _, con, _ = ticket_con_pasos
    _como(tenant, "approver")

    r = await client.get(f"/api/v1/audit/steps/{con}")

    assert r.status_code == 403, r.text
