"""Contexto de red en audit.audit_events (KC-DB-05-6).

Requiere db/migrations/029 aplicada. Comprueba contra la base real (el test unitario
test_audit_events_network usa un doble y nunca ejecuta el INSERT) que ip, user_agent y
request_id se guardan, que la cadena sigue íntegra y que seal_event conserva el
endurecimiento de KC-DB-05-1.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from kallicode_core import db
from kallicode_core.auditoria import registrar_evento
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integracion


@pytest.fixture
async def tenant() -> AsyncIterator[str]:
    t = f"org_red_{uuid.uuid4().hex[:10]}"
    async with db.sesion_sistema() as admin:
        await admin.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org prueba red')"),
            {"t": t},
        )
    try:
        yield t
    finally:
        async with db.sesion_sistema() as admin:
            await admin.execute(
                text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable")
            )
            await admin.execute(
                text("DELETE FROM audit.audit_events WHERE tenant_id = :t"), {"t": t}
            )
            await admin.execute(
                text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable")
            )
            await admin.execute(
                text("DELETE FROM audit.chain_heads WHERE tenant_id = :t"), {"t": t}
            )
            await admin.execute(text("DELETE FROM core.organizations WHERE id = :t"), {"t": t})


async def _verificar(s: AsyncSession, tenant: str) -> tuple[bool, int]:
    fila = (
        await s.execute(
            text("SELECT integra, eventos_verificados FROM audit.verify_chain(:t)"), {"t": tenant}
        )
    ).one()
    return fila[0], fila[1]


async def test_un_evento_con_contexto_de_red_se_guarda_y_la_cadena_sigue_integra(
    tenant: str,
) -> None:
    req = str(uuid.uuid4())
    async with db.sesion_tenant(tenant) as s:
        sello = await registrar_evento(
            s,
            tenant,
            evento="ticket_creado",
            resumen="Ticket de prueba",
            actor_tipo="humano",
            actor_id="user_1",
            ip="192.168.1.100",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            request_id=req,
        )
    assert len(sello) == 64

    async with db.sesion_tenant(tenant) as s:
        fila = (
            await s.execute(
                text(
                    "SELECT host(ip), user_agent, request_id::text FROM audit.audit_events "
                    "WHERE sello = :sl"
                ),
                {"sl": sello},
            )
        ).one()
        assert tuple(fila) == ("192.168.1.100", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", req)
        assert await _verificar(s, tenant) == (True, 1)


async def test_un_evento_sin_contexto_de_red_admite_nulos(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        sello = await registrar_evento(
            s, tenant, evento="paso_llm", resumen="Paso de agente", actor_tipo="agente"
        )
    async with db.sesion_tenant(tenant) as s:
        fila = (
            await s.execute(
                text("SELECT ip, user_agent, request_id FROM audit.audit_events WHERE sello = :sl"),
                {"sl": sello},
            )
        ).one()
        assert tuple(fila) == (None, None, None)


async def test_una_cadena_con_eventos_con_y_sin_contexto_de_red_es_integra(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        await registrar_evento(s, tenant, evento="ticket_creado", resumen="1", actor_tipo="humano")
    async with db.sesion_tenant(tenant) as s:
        await registrar_evento(
            s,
            tenant,
            evento="ticket_creado",
            resumen="2",
            actor_tipo="humano",
            ip="2001:db8::1",
            user_agent="curl/8.0",
            request_id=str(uuid.uuid4()),
        )
    async with db.sesion_tenant(tenant) as s:
        await registrar_evento(s, tenant, evento="paso_llm", resumen="3", actor_tipo="agente")
    async with db.sesion_tenant(tenant) as s:
        assert await _verificar(s, tenant) == (True, 3)


async def test_una_ip_invalida_se_rechaza_y_no_deja_evento(tenant: str) -> None:
    with pytest.raises(DBAPIError):
        async with db.sesion_tenant(tenant) as s:
            await registrar_evento(
                s,
                tenant,
                evento="ticket_creado",
                resumen="ip mala",
                actor_tipo="humano",
                ip="no-es-una-ip",
            )
    async with db.sesion_tenant(tenant) as s:
        n: int = (await s.execute(text("SELECT count(*) FROM audit.audit_events"))).scalar_one()
    assert n == 0


async def test_seal_event_conserva_el_endurecimiento_de_kc_db_05_1() -> None:
    """Red de seguridad: una migración posterior no puede devolver seal_event a una versión
    que dependa de pgcrypto (digest) ni quitarle SECURITY DEFINER, el search_path fijo o la
    guarda de tenant."""
    async with db.sesion_sistema() as admin:
        fila = (
            await admin.execute(
                text(
                    "SELECT prosecdef, proconfig, prosrc FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = 'audit' AND p.proname = 'seal_event'"
                )
            )
        ).one()
    definer, config, fuente = fila
    assert definer is True
    assert config == ["search_path=pg_catalog, pg_temp"]
    assert "digest(" not in fuente, "seal_event no puede depender de pgcrypto"
    assert "otro tenant" in fuente, "seal_event debe rechazar eventos de otro tenant"


async def test_las_columnas_nuevas_existen_con_su_tipo() -> None:
    async with db.sesion_sistema() as admin:
        tipos: dict[str, str] = dict(
            (
                await admin.execute(
                    text(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = 'audit' AND table_name = 'audit_events' "
                        "AND column_name IN ('ip', 'user_agent', 'request_id')"
                    )
                )
            ).all()
        )
    assert tipos == {"ip": "inet", "user_agent": "text", "request_id": "uuid"}
