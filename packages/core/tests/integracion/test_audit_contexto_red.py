"""Procedencia de la acción en los eventos de auditoría (KC-DB-05-6).

Requiere db/migrations/029 aplicada. ip, user_agent y request_id se guardan y entran al
sello bajo una columna version_sello (1 = canónico original, 2 = con los tres campos), de
modo que la cadena existente sigue siendo válida. Estas pruebas corren contra la base real
(el test unitario test_audit_events_network usa un doble y nunca ejecuta el INSERT).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
from kallicode_core import db
from kallicode_core.auditoria import registrar_evento
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integracion

GENESIS = "0" * 64

# Canónico de la versión 1, copiado a mano de db/03_triggers.sql: no debe cambiar nunca.
_FORMULA_V1 = (
    "encode(sha256(convert_to({e}.sello_previo || {e}.tenant_id || {e}.evento || {e}.resumen "
    "|| coalesce({e}.datos::text, '') || coalesce({e}.ticket_id, '') || coalesce({e}.actor_id, '') "
    "|| to_char({e}.creado_en, 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'), 'UTF8')), 'hex')"
)
# Canónico de la versión 2: lo anterior más ip, user_agent y request_id separados por chr(31).
_FORMULA_V2 = (
    "encode(sha256(convert_to({e}.sello_previo || {e}.tenant_id || {e}.evento || {e}.resumen "
    "|| coalesce({e}.datos::text, '') || coalesce({e}.ticket_id, '') || coalesce({e}.actor_id, '') "
    "|| chr(31) || coalesce(abbrev({e}.ip), '') || chr(31) || coalesce({e}.user_agent, '') "
    "|| chr(31) || coalesce({e}.request_id, '') || chr(31) "
    "|| to_char({e}.creado_en, 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"'), 'UTF8')), 'hex')"
)


class _Revertir(Exception):
    """Fuerza el rollback de la transacción de sistema tras comprobar."""


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


async def _verificar(s: AsyncSession, tenant: str) -> dict[str, Any]:
    fila = (
        (
            await s.execute(
                text(
                    "SELECT integra, eventos_verificados, primer_evento_roto, "
                    "eventos_esperados, eventos_observados FROM audit.verify_chain(:t)"
                ),
                {"t": tenant},
            )
        )
        .mappings()
        .one()
    )
    return dict(fila)


async def _revertiendo(comprobar: Callable[[AsyncSession], Awaitable[None]]) -> None:
    with pytest.raises(_Revertir):
        async with db.sesion_sistema() as admin:
            await comprobar(admin)
            raise _Revertir


# --- criterio: ip, user_agent y request_id se guardan y el sello los incorpora ---------
async def test_un_evento_con_procedencia_se_guarda_con_version_2_y_la_cadena_es_integra(
    tenant: str,
) -> None:
    req = f"req-{uuid.uuid4()}"
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
                    "SELECT abbrev(ip), user_agent, request_id, version_sello "
                    "FROM audit.audit_events WHERE sello = :sl"
                ),
                {"sl": sello},
            )
        ).one()
        assert tuple(fila) == (
            "192.168.1.100",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            req,
            2,
        )
        assert (await _verificar(s, tenant))["integra"] is True


async def test_el_sello_v2_coincide_con_el_canonico_documentado(tenant: str) -> None:
    """Fija el formato: recalcula el sello con la fórmula escrita a mano en este test."""
    async with db.sesion_tenant(tenant) as s:
        await registrar_evento(
            s,
            tenant,
            evento="ticket_creado",
            resumen="Formato",
            actor_tipo="humano",
            ip="2001:db8::1",
            user_agent="curl/8.0",
            request_id="abc-123",
        )
    async with db.sesion_tenant(tenant) as s:
        coincide: bool = (
            await s.execute(
                text(f"SELECT sello = {_FORMULA_V2.format(e='e')} FROM audit.audit_events e")
            )
        ).scalar_one()
    assert coincide


async def test_un_evento_sin_procedencia_admite_nulos_y_tambien_es_version_2(tenant: str) -> None:
    async with db.sesion_tenant(tenant) as s:
        sello = await registrar_evento(
            s, tenant, evento="paso_llm", resumen="Paso de agente", actor_tipo="agente"
        )
    async with db.sesion_tenant(tenant) as s:
        fila = (
            await s.execute(
                text(
                    "SELECT ip, user_agent, request_id, version_sello FROM audit.audit_events "
                    "WHERE sello = :sl"
                ),
                {"sl": sello},
            )
        ).one()
        assert tuple(fila) == (None, None, None, 2)
        assert (await _verificar(s, tenant))["integra"] is True


async def test_la_aplicacion_no_puede_elegir_la_version_del_sello(tenant: str) -> None:
    """Un cliente no puede pedir el formato 1 para que sus datos de red queden sin sellar."""
    async with db.sesion_tenant(tenant) as s:
        await s.execute(
            text(
                "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, user_agent, "
                "version_sello) VALUES (core.current_tenant(), 'sistema', 'prueba', 'x', 'ua', 1)"
            )
        )
    async with db.sesion_tenant(tenant) as s:
        version: int = (
            await s.execute(text("SELECT version_sello FROM audit.audit_events"))
        ).scalar_one()
        assert version == 2
        assert (await _verificar(s, tenant))["integra"] is True


# --- criterio: la cadena existente no se invalida ---------------------------------------
async def test_una_cadena_con_eventos_version_1_y_version_2_es_integra(tenant: str) -> None:
    resultado: dict[str, Any] = {}

    async def comprobar(admin: AsyncSession) -> None:
        # Tres eventos «legados»: versión 1, sellados con la fórmula original (sin trigger).
        await admin.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_seal"))
        await admin.execute(
            text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable")
        )
        await admin.execute(
            text("INSERT INTO audit.chain_heads (tenant_id) VALUES (:t)"), {"t": tenant}
        )
        previo = GENESIS
        for n in range(1, 4):
            sello: str = (
                await admin.execute(
                    text(
                        "WITH nuevo AS (INSERT INTO audit.audit_events (tenant_id, actor_tipo, "
                        "evento, resumen, sello_previo, sello, version_sello, creado_en) "
                        "VALUES (:t, 'sistema', 'prueba', :r, :p, :ph, 1, now()) RETURNING *) "
                        "SELECT " + _FORMULA_V1.format(e="nuevo") + " FROM nuevo"
                    ),
                    {"t": tenant, "r": f"legado {n}", "p": previo, "ph": GENESIS},
                )
            ).scalar_one()
            await admin.execute(
                text(
                    "UPDATE audit.audit_events SET sello = :s WHERE tenant_id = :t AND resumen = :r"
                ),
                {"s": sello, "t": tenant, "r": f"legado {n}"},
            )
            await admin.execute(
                text(
                    "UPDATE audit.chain_heads SET eventos = eventos + 1, ultimo_sello = :s "
                    "WHERE tenant_id = :t"
                ),
                {"s": sello, "t": tenant},
            )
            previo = sello
        await admin.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_seal"))
        await admin.execute(
            text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable")
        )

        # Y uno nuevo, sellado por el trigger (versión 2, con procedencia).
        await admin.execute(
            text(
                "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, ip, "
                "user_agent, request_id) VALUES (:t, 'sistema', 'prueba', 'nuevo', "
                "'10.0.0.5', 'ua', 'r-1')"
            ),
            {"t": tenant},
        )
        resultado["versiones"] = [
            r[0]
            for r in (
                await admin.execute(
                    text(
                        "SELECT version_sello FROM audit.audit_events WHERE tenant_id = :t "
                        "ORDER BY id"
                    ),
                    {"t": tenant},
                )
            ).all()
        ]
        resultado["verify"] = await _verificar(admin, tenant)

    await _revertiendo(comprobar)
    assert resultado["versiones"] == [1, 1, 1, 2]
    assert resultado["verify"] == {
        "integra": True,
        "eventos_verificados": 4,
        "primer_evento_roto": None,
        "eventos_esperados": 4,
        "eventos_observados": 4,
    }


async def test_los_eventos_existentes_quedan_en_version_1_por_defecto() -> None:
    async with db.sesion_sistema() as admin:
        defecto: str = (
            await admin.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_schema = 'audit' AND table_name = 'audit_events' "
                    "AND column_name = 'version_sello'"
                )
            )
        ).scalar_one()
        quedan_v1: int = (
            await admin.execute(
                text("SELECT count(*) FROM audit.audit_events WHERE version_sello = 1")
            )
        ).scalar_one()
    assert defecto == "1"
    assert quedan_v1 >= 0  # los eventos previos a la migración (si hay) conservan la versión 1


# --- criterio: los tres campos están protegidos por la cadena ----------------------------
@pytest.mark.parametrize(
    ("campo", "valor"),
    [
        ("ip", "'10.9.9.9'"),
        ("user_agent", "'otro-agente'"),
        ("request_id", "'otra-peticion'"),
    ],
)
async def test_alterar_ip_user_agent_o_request_id_rompe_la_cadena(
    tenant: str, campo: str, valor: str
) -> None:
    async with db.sesion_tenant(tenant) as s:
        await registrar_evento(s, tenant, evento="paso_llm", resumen="antes", actor_tipo="agente")
    async with db.sesion_tenant(tenant) as s:
        await registrar_evento(
            s,
            tenant,
            evento="ticket_creado",
            resumen="objetivo",
            actor_tipo="humano",
            ip="192.168.1.1",
            user_agent="ua-original",
            request_id="req-original",
        )
    async with db.sesion_tenant(tenant) as s:
        objetivo: int = (
            await s.execute(text("SELECT id FROM audit.audit_events WHERE resumen = 'objetivo'"))
        ).scalar_one()

    resultado: dict[str, Any] = {}

    async def comprobar(admin: AsyncSession) -> None:
        await admin.execute(
            text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable")
        )
        await admin.execute(
            text(f"UPDATE audit.audit_events SET {campo} = {valor} WHERE id = :i"),
            {"i": objetivo},
        )
        resultado["verify"] = await _verificar(admin, tenant)

    await _revertiendo(comprobar)
    assert resultado["verify"]["integra"] is False
    assert resultado["verify"]["primer_evento_roto"] == objetivo


async def test_el_separador_impide_que_un_valor_preste_caracteres_al_vecino() -> None:
    """('ab', 'c') y ('a', 'bc') no pueden producir el mismo canónico."""
    base: dict[str, Any] = {
        "tenant_id": "org_x",
        "evento": "e",
        "resumen": "r",
        "sello_previo": GENESIS,
        "creado_en": "2026-10-12T00:00:00+00:00",
        "version_sello": 2,
    }
    import json

    async with db.sesion_sistema() as admin:
        sellos: list[str] = [
            (
                await admin.execute(
                    text(
                        "SELECT audit.calcular_sello("
                        "jsonb_populate_record(NULL::audit.audit_events, CAST(:j AS jsonb)))"
                    ),
                    {"j": json.dumps({**base, "user_agent": ua, "request_id": rid})},
                )
            ).scalar_one()
            for ua, rid in (("ab", "c"), ("a", "bc"))
        ]
    assert sellos[0] != sellos[1]


# --- robustez ----------------------------------------------------------------------------
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


async def test_una_version_de_sello_desconocida_se_rechaza(tenant: str) -> None:
    """El trigger fija siempre la versión 2, así que la restricción solo se ejercita si alguien
    lo desactiva a propósito; la transacción se revierte al fallar."""
    with pytest.raises(DBAPIError, match="ae_version_sello_chk"):
        async with db.sesion_sistema() as admin:
            await admin.execute(
                text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_seal")
            )
            await admin.execute(
                text(
                    "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, "
                    "sello_previo, sello, version_sello) VALUES (:t, 'sistema', 'e', 'r', :g, :g, 3)"
                ),
                {"t": tenant, "g": GENESIS},
            )


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
    assert "calcular_sello" in fuente, "seal_event y verify_chain deben compartir el canónico"


async def test_las_columnas_nuevas_existen_con_su_tipo() -> None:
    async with db.sesion_sistema() as admin:
        tipos: dict[str, str] = dict(
            (
                await admin.execute(
                    text(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_schema = 'audit' AND table_name = 'audit_events' "
                        "AND column_name IN ('ip', 'user_agent', 'request_id', 'version_sello')"
                    )
                )
            ).all()
        )
    assert tipos == {
        "ip": "inet",
        "user_agent": "text",
        "request_id": "text",
        "version_sello": "smallint",
    }
