"""Roles de base de datos y aislamiento con un rol no privilegiado (KC-DB-01-5).

Requiere el esquema aplicado con db/00_roles.sql + 00_extensiones.sql (como
superusuario) y 01..05.sql como kallicode_admin, igual que el CI y `make db`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from kallicode_core import db
from sqlalchemy import text

pytestmark = pytest.mark.integracion

ROLES = ("kallicode_app", "kallicode_worker", "kallicode_admin")


async def test_atributos_de_los_roles() -> None:
    async with db.sesion_sistema() as admin:
        filas = await db.todos(
            admin,
            "SELECT rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole "
            "FROM pg_roles WHERE rolname = ANY(:roles)",
            {"roles": list(ROLES)},
        )
    roles = {f["rolname"]: f for f in filas}

    assert set(roles) == set(ROLES), "falta algún rol: aplica db/00_roles.sql"
    for nombre, r in roles.items():
        assert r["rolcanlogin"], f"{nombre} debe poder hacer login"
        assert not r["rolsuper"], f"{nombre} no puede ser superusuario"
        assert not r["rolcreatedb"], f"{nombre} no puede crear bases"
        assert not r["rolcreaterole"], f"{nombre} no puede crear roles"
    assert not roles["kallicode_app"]["rolbypassrls"]
    assert not roles["kallicode_worker"]["rolbypassrls"]
    assert roles["kallicode_admin"]["rolbypassrls"]


async def test_solo_kallicode_admin_tiene_bypassrls() -> None:
    async with db.sesion_sistema() as admin:
        filas = await db.todos(
            admin,
            "SELECT rolname FROM pg_roles "
            "WHERE rolbypassrls AND NOT rolsuper AND rolname LIKE 'kallicode%'",
        )
    assert [f["rolname"] for f in filas] == ["kallicode_admin"]


async def test_el_esquema_es_de_kallicode_admin() -> None:
    """Demuestra que 01..05.sql se aplicaron sin superusuario."""
    async with db.sesion_sistema() as admin:
        filas = await db.todos(
            admin,
            "SELECT DISTINCT tableowner FROM pg_tables WHERE schemaname IN ('core','vec','audit')",
        )
    assert [f["tableowner"] for f in filas] == ["kallicode_admin"], (
        "el esquema no lo construyó kallicode_admin; reconstruye con `make clean && make db`"
    )


@pytest.fixture
async def ticket_de_un_tenant() -> AsyncIterator[tuple[str, str]]:
    """Ticket real insertado con el admin: sin datos, una prueba de 'cero filas'
    pasaría aunque RLS no existiera."""
    sufijo = uuid.uuid4().hex[:12]
    tenant, ticket = f"org_rls_{sufijo}", f"tk_rls_{sufijo}"
    async with db.sesion_sistema() as admin:
        await admin.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org prueba RLS')"),
            {"t": tenant},
        )
        await admin.execute(
            text(
                "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion) "
                "VALUES (:id, :t, 'bug', 'Ticket prueba RLS', 'KC-DB-01-5')"
            ),
            {"id": ticket, "t": tenant},
        )
    try:
        yield tenant, ticket
    finally:
        async with db.sesion_sistema() as admin:
            await admin.execute(text("DELETE FROM core.tickets WHERE id = :id"), {"id": ticket})
            for tabla in ("core.ticket_counters", "core.organizations"):
                columna = "id" if tabla == "core.organizations" else "tenant_id"
                await admin.execute(
                    text(f"DELETE FROM {tabla} WHERE {columna} = :t"), {"t": tenant}
                )


async def test_app_sin_tenant_no_ve_tickets_existentes(
    ticket_de_un_tenant: tuple[str, str],
) -> None:
    tenant, ticket = ticket_de_un_tenant

    # Control: el ticket existe (el admin lo ve).
    async with db.sesion_sistema() as admin:
        assert (
            await db.valor(
                admin, "SELECT count(*) FROM core.tickets WHERE id = :id", {"id": ticket}
            )
            == 1
        )

    # kallicode_app sin app.tenant_id: ve cero filas en lugar de todas.
    maker = await db._maker_app()
    async with maker() as sesion, sesion.begin():
        assert await db.valor(sesion, "SELECT current_user") == "kallicode_app"
        assert await db.valor(sesion, "SELECT count(*) FROM core.tickets") == 0

    # Control positivo: con su tenant sí lo ve (RLS no bloquea todo).
    async with db.sesion_tenant(tenant) as sesion:
        assert (
            await db.valor(
                sesion, "SELECT count(*) FROM core.tickets WHERE id = :id", {"id": ticket}
            )
            == 1
        )
