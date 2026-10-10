"""Cadena de auditoría: verify_chain verifica el encadenamiento y chain_heads
queda protegida (KC-DB-05-1, cierra DB-F04).

Requiere db/migrations/024 aplicada. Los casos que manipulan la cadena corren
dentro de una transacción de sistema que se revierte: no dejan triggers
desactivados ni eventos borrados en la base.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from itertools import pairwise
from typing import Any

import pytest
from kallicode_core import db
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integracion

GENESIS = "0" * 64
N_EVENTOS = 10


class _Revertir(Exception):
    """Fuerza el rollback de la transacción de sistema tras comprobar."""


async def _evento(tenant: str, n: int) -> None:
    async with db.sesion_tenant(tenant) as s:
        await s.execute(
            text(
                "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, datos) "
                "VALUES (core.current_tenant(), 'sistema', 'prueba_cadena', :r, "
                "CAST(:d AS jsonb))"
            ),
            {"r": f"evento {n}", "d": f'{{"n": {n}}}'},
        )


async def _ids(tenant: str) -> list[int]:
    async with db.sesion_tenant(tenant) as s:
        filas = await s.execute(
            text("SELECT id FROM audit.audit_events ORDER BY id"),
        )
        return [r[0] for r in filas.all()]


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


async def _v16_v17(s: AsyncSession) -> dict[str, int]:
    filas = await s.execute(
        text(
            "SELECT chequeo, problemas FROM core.validar_integridad() "
            "WHERE chequeo IN ('V16_cadena_auditoria', 'V17_cabeza_cadena')"
        )
    )
    return {r[0]: int(r[1]) for r in filas.all()}


async def _borrar_evento(s: AsyncSession, evento_id: int) -> None:
    """Borra un evento como lo haría un rol privilegiado (desactiva el trigger)."""
    await s.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
    await s.execute(text("DELETE FROM audit.audit_events WHERE id = :i"), {"i": evento_id})
    await s.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))


async def _revertiendo(
    comprobar: Callable[[AsyncSession], Awaitable[None]],
) -> None:
    """Ejecuta `comprobar` en una sesión de sistema y revierte todo al terminar."""
    with pytest.raises(_Revertir):
        async with db.sesion_sistema() as admin:
            await comprobar(admin)
            raise _Revertir


@pytest.fixture
async def cadena() -> AsyncIterator[tuple[str, list[int]]]:
    """Un tenant con una cadena de diez eventos sellados por la aplicación."""
    t = f"org_cad_{uuid.uuid4().hex[:10]}"
    async with db.sesion_sistema() as admin:
        await admin.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org prueba cadena')"),
            {"t": t},
        )
    for n in range(1, N_EVENTOS + 1):
        await _evento(t, n)
    try:
        yield t, await _ids(t)
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


# --- criterio 1: verify_chain arrastra el sello anterior y exige génesis -----
async def test_cadena_intacta_es_integra_y_encadena_desde_el_genesis(
    cadena: tuple[str, list[int]],
) -> None:
    tenant, ids = cadena
    assert len(ids) == N_EVENTOS
    async with db.sesion_tenant(tenant) as s:
        r = await _verificar(s, tenant)
        assert r == {
            "integra": True,
            "eventos_verificados": N_EVENTOS,
            "primer_evento_roto": None,
            "eventos_esperados": N_EVENTOS,
            "eventos_observados": N_EVENTOS,
        }
        sellos = (
            await s.execute(text("SELECT sello_previo, sello FROM audit.audit_events ORDER BY id"))
        ).all()
    assert sellos[0][0] == GENESIS
    for anterior, actual in pairwise(sellos):
        assert actual[0] == anterior[1], "sello_previo debe ser el sello del evento anterior"


async def test_ventana_sin_eventos_es_integra_y_no_tiene_recuento_esperado(
    cadena: tuple[str, list[int]],
) -> None:
    tenant, _ = cadena
    async with db.sesion_tenant(tenant) as s:
        r = (
            await s.execute(
                text(
                    "SELECT integra, eventos_verificados, eventos_esperados "
                    "FROM audit.verify_chain(:t, now() + interval '1 day', NULL)"
                ),
                {"t": tenant},
            )
        ).one()
    assert tuple(r) == (True, 0, None)


# --- criterio 2: borrar el quinto de diez se detecta y señala el sexto ------
async def test_borrar_el_quinto_rompe_la_cadena_y_senala_el_sexto(
    cadena: tuple[str, list[int]],
) -> None:
    tenant, ids = cadena
    quinto, sexto = ids[4], ids[5]

    async def comprobar(admin: AsyncSession) -> None:
        await _borrar_evento(admin, quinto)
        r = await _verificar(admin, tenant)
        assert r["integra"] is False
        assert r["primer_evento_roto"] == sexto
        assert (r["eventos_esperados"], r["eventos_observados"]) == (N_EVENTOS, N_EVENTOS - 1)

    await _revertiendo(comprobar)
    # Revertido: la cadena real sigue intacta.
    async with db.sesion_tenant(tenant) as s:
        assert (await _verificar(s, tenant))["integra"] is True


async def test_borrar_el_primero_senala_el_segundo(cadena: tuple[str, list[int]]) -> None:
    tenant, ids = cadena

    async def comprobar(admin: AsyncSession) -> None:
        await _borrar_evento(admin, ids[0])
        r = await _verificar(admin, tenant)
        assert (r["integra"], r["primer_evento_roto"]) == (False, ids[1])

    await _revertiendo(comprobar)


async def test_truncar_la_cola_se_delata_por_eventos_esperados_vs_observados(
    cadena: tuple[str, list[int]],
) -> None:
    tenant, ids = cadena

    async def comprobar(admin: AsyncSession) -> None:
        await _borrar_evento(admin, ids[-1])
        r = await _verificar(admin, tenant)
        # No hay eslabón «roto» (los anteriores siguen encadenados), pero faltan eventos.
        assert r["integra"] is False
        assert r["primer_evento_roto"] is None
        assert (r["eventos_esperados"], r["eventos_observados"]) == (N_EVENTOS, N_EVENTOS - 1)

    await _revertiendo(comprobar)


async def test_un_sello_alterado_se_detecta_en_su_evento(cadena: tuple[str, list[int]]) -> None:
    tenant, ids = cadena

    async def comprobar(admin: AsyncSession) -> None:
        await admin.execute(
            text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable")
        )
        await admin.execute(
            text("UPDATE audit.audit_events SET resumen = 'alterado' WHERE id = :i"),
            {"i": ids[2]},
        )
        r = await _verificar(admin, tenant)
        assert (r["integra"], r["primer_evento_roto"]) == (False, ids[2])

    await _revertiendo(comprobar)


# --- criterio 3: la aplicación pierde el UPDATE de chain_heads ----------------
@pytest.mark.parametrize(
    "sentencia",
    [
        "UPDATE audit.chain_heads SET ultimo_sello = repeat('a', 64)",
        "UPDATE audit.chain_heads SET eventos = 0",
        "DELETE FROM audit.chain_heads",
        "INSERT INTO audit.chain_heads (tenant_id) VALUES (core.current_tenant())",
    ],
)
async def test_la_aplicacion_no_puede_escribir_chain_heads(
    cadena: tuple[str, list[int]], sentencia: str
) -> None:
    tenant, _ = cadena
    with pytest.raises(DBAPIError, match="permission denied"):
        async with db.sesion_tenant(tenant) as s:
            await s.execute(text(sentencia))


async def test_la_aplicacion_sigue_pudiendo_leer_chain_heads_y_sellar_eventos(
    cadena: tuple[str, list[int]],
) -> None:
    tenant, _ = cadena
    await _evento(tenant, 99)  # sella por SECURITY DEFINER aunque no pueda escribir la cabeza
    async with db.sesion_tenant(tenant) as s:
        eventos: int = (await s.execute(text("SELECT eventos FROM audit.chain_heads"))).scalar_one()
    assert eventos == N_EVENTOS + 1


async def test_seal_event_rechaza_sellar_un_evento_de_otro_tenant(
    cadena: tuple[str, list[int]],
) -> None:
    tenant, _ = cadena
    with pytest.raises(DBAPIError):
        async with db.sesion_tenant(tenant) as s:
            await s.execute(
                text(
                    "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen) "
                    "VALUES ('org_ajena_x', 'sistema', 'intruso', 'x')"
                )
            )


# --- criterio 3 (cont.): el trigger rechaza cualquier retroceso ---------------
async def test_trigger_rechaza_retroceder_o_reescribir_la_cabeza(
    cadena: tuple[str, list[int]],
) -> None:
    tenant, _ = cadena
    intentos = [
        ("UPDATE audit.chain_heads SET eventos = eventos - 1 WHERE tenant_id = :t", "uno en uno"),
        ("UPDATE audit.chain_heads SET eventos = eventos + 2 WHERE tenant_id = :t", "uno en uno"),
        (
            "UPDATE audit.chain_heads SET ultimo_sello = repeat('a', 64) WHERE tenant_id = :t",
            "uno en uno",
        ),
        ("DELETE FROM audit.chain_heads WHERE tenant_id = :t", "aun tiene eventos"),
    ]
    for sentencia, mensaje in intentos:
        with pytest.raises(DBAPIError, match=mensaje):
            async with db.sesion_sistema() as admin:
                await admin.execute(text(sentencia), {"t": tenant})


async def test_existe_el_trigger_que_prohibe_truncar_la_cabeza() -> None:
    async with db.sesion_sistema() as admin:
        n: int = (
            await admin.execute(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'audit.chain_heads'::regclass "
                    "AND tgname = 'trg_chain_heads_no_truncate' AND (tgtype & 32) = 32"
                )
            )
        ).scalar_one()
    assert n == 1


async def test_la_cabeza_avanza_de_uno_en_uno_con_el_sello_del_ultimo_evento(
    cadena: tuple[str, list[int]],
) -> None:
    tenant, ids = cadena
    async with db.sesion_tenant(tenant) as s:
        cabeza = (
            await s.execute(text("SELECT eventos, ultimo_sello FROM audit.chain_heads"))
        ).one()
        ultimo: str = (
            await s.execute(
                text("SELECT sello FROM audit.audit_events WHERE id = :i"), {"i": ids[-1]}
            )
        ).scalar_one()
    assert cabeza[0] == N_EVENTOS
    assert cabeza[1] == ultimo


# --- criterio 4: V16 y V17 sobre la función corregida --------------------------
async def test_v16_y_v17_detectan_un_evento_borrado(cadena: tuple[str, list[int]]) -> None:
    _, ids = cadena

    async def comprobar(admin: AsyncSession) -> None:
        antes = await _v16_v17(admin)
        await _borrar_evento(admin, ids[4])
        despues = await _v16_v17(admin)
        assert despues["V16_cadena_auditoria"] == antes["V16_cadena_auditoria"] + 1
        assert despues["V17_cabeza_cadena"] == antes["V17_cabeza_cadena"] + 1

    await _revertiendo(comprobar)


async def test_v17_detecta_una_cabeza_manipulada_y_un_tenant_sin_cabeza(
    cadena: tuple[str, list[int]],
) -> None:
    tenant, _ = cadena

    async def cabeza_manipulada(admin: AsyncSession) -> None:
        antes = await _v16_v17(admin)
        await admin.execute(
            text("ALTER TABLE audit.chain_heads DISABLE TRIGGER trg_chain_heads_guard")
        )
        await admin.execute(
            text(
                "UPDATE audit.chain_heads SET ultimo_sello = repeat('b', 64) WHERE tenant_id = :t"
            ),
            {"t": tenant},
        )
        assert (await _v16_v17(admin))["V17_cabeza_cadena"] == antes["V17_cabeza_cadena"] + 1

    await _revertiendo(cabeza_manipulada)

    async def sin_cabeza(admin: AsyncSession) -> None:
        antes = await _v16_v17(admin)
        await admin.execute(
            text("ALTER TABLE audit.chain_heads DISABLE TRIGGER trg_chain_heads_guard")
        )
        await admin.execute(
            text("DELETE FROM audit.chain_heads WHERE tenant_id = :t"), {"t": tenant}
        )
        assert (await _v16_v17(admin))["V17_cabeza_cadena"] == antes["V17_cabeza_cadena"] + 1

    await _revertiendo(sin_cabeza)


async def test_v16_y_v17_dan_cero_con_una_cadena_intacta(cadena: tuple[str, list[int]]) -> None:
    tenant, _ = cadena
    async with db.sesion_sistema() as admin:
        # Sobre la cadena de este tenant: ni V16 ni V17 la cuentan como problema.
        r = await _verificar(admin, tenant)
        assert r["integra"] is True
        fila: int = (
            await admin.execute(
                text(
                    "SELECT count(*) FROM audit.chain_heads h, "
                    "LATERAL audit.verify_chain(h.tenant_id) v "
                    "WHERE h.tenant_id = :t AND NOT v.integra"
                ),
                {"t": tenant},
            )
        ).scalar_one()
    assert fila == 0
