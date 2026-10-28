"""V21: coherencia de tenant entre padre e hijo (KC-DB-03-3, DB-F01).

Requiere db/migrations/011_v21_coherencia_tenant.sql aplicada. Se ejecuta con
kallicode_admin (BYPASSRLS) para ver las filas de todos los tenants.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from kallicode_core import db
from sqlalchemy import text

pytestmark = pytest.mark.integracion


async def _v21(sesion) -> dict:
    return await db.uno(
        sesion,
        "SELECT problemas, descripcion FROM core.validar_integridad() "
        "WHERE chequeo = 'V21_coherencia_tenant'",
    )


@pytest.fixture
async def ticket_de_b() -> AsyncIterator[tuple[str, str, str]]:
    """Tenants A y B; B tiene un ticket. Devuelve (tenant_a, tenant_b, ticket_b)."""
    sufijo = uuid.uuid4().hex[:10]
    tenant_a, tenant_b, ticket_b = f"org_v21a_{sufijo}", f"org_v21b_{sufijo}", f"tk_v21_{sufijo}"
    async with db.sesion_sistema() as admin:
        for t in (tenant_a, tenant_b):
            await admin.execute(
                text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org prueba V21')"),
                {"t": t},
            )
        await admin.execute(
            text(
                "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion) "
                "VALUES (:id, :t, 'bug', 'Ticket prueba V21', 'KC-DB-03-3')"
            ),
            {"id": ticket_b, "t": tenant_b},
        )
    try:
        yield tenant_a, tenant_b, ticket_b
    finally:
        async with db.sesion_sistema() as admin:
            for tabla in ("core.specs", "core.tickets", "core.ticket_counters"):
                await admin.execute(
                    text(f"DELETE FROM {tabla} WHERE tenant_id IN (:a, :b)"),
                    {"a": tenant_a, "b": tenant_b},
                )
            await admin.execute(
                text("DELETE FROM core.organizations WHERE id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )


async def test_v21_es_cero_con_datos_coherentes(ticket_de_b) -> None:
    async with db.sesion_sistema() as admin:
        v21 = await _v21(admin)
    assert v21 is not None, "V21 no existe: aplica db/migrations/011_v21_coherencia_tenant.sql"
    assert v21["problemas"] == 0, v21["descripcion"]


async def test_v21_detecta_y_nombra_una_spec_colgada_de_otro_tenant(ticket_de_b) -> None:
    """Desde KC-DB-03-2 la FK compuesta rechaza esta fila, así que se reproduce la
    ventana NOT VALID -> VALIDATE (o una FK desactivada y recreada): la fila cruzada
    ya existe y la FK no la revisó. V21 debe detectarla. Todo se deshace al final."""
    tenant_a, tenant_b, ticket_b = ticket_de_b
    spec = f"sp_v21_{uuid.uuid4().hex[:10]}"
    maker = await db._maker_admin()
    async with maker() as admin:
        tx = await admin.begin()
        try:
            await admin.execute(text("ALTER TABLE core.specs DROP CONSTRAINT specs_ticket_id_fkey"))
            await admin.execute(
                text(
                    "INSERT INTO core.specs (id, tenant_id, ticket_id, version, spec_md_ref, "
                    "scope_paths) VALUES (:id, :a, :tk, 1, 'blob/spec.md', '{}')"
                ),
                {"id": spec, "a": tenant_a, "tk": ticket_b},
            )
            await admin.execute(
                text(
                    "ALTER TABLE core.specs ADD CONSTRAINT specs_ticket_id_fkey "
                    "FOREIGN KEY (tenant_id, ticket_id) REFERENCES core.tickets (tenant_id, id) "
                    "ON DELETE CASCADE NOT VALID"
                )
            )
            v21 = await _v21(admin)
            filas = await db.todos(
                admin,
                "SELECT * FROM core.incoherencias_tenant() WHERE fila->>'id' = :id",
                {"id": spec},
            )
        finally:
            await tx.rollback()

    assert v21["problemas"] >= 1
    assert "core.specs" in v21["descripcion"], v21["descripcion"]
    assert len(filas) == 1
    assert filas[0]["tabla_hija"] == "core.specs"
    assert filas[0]["tabla_padre"] == "core.tickets"
    assert (filas[0]["tenant_hija"], filas[0]["tenant_padre"]) == (tenant_a, tenant_b)


async def test_v21_cubre_una_tabla_nueva_sin_tocarla() -> None:
    """Las relaciones salen de pg_constraint: una FK creada después (aquí, en una
    transacción que se deshace) queda revisada sin modificar V21."""
    maker = await db._maker_admin()
    async with maker() as sesion:
        tx = await sesion.begin()
        try:
            for sql in (
                "CREATE TABLE core.v21_padre (id text PRIMARY KEY, tenant_id text NOT NULL)",
                "CREATE TABLE core.v21_hija (id text PRIMARY KEY, tenant_id text NOT NULL, "
                "padre_id text REFERENCES core.v21_padre(id))",
                "INSERT INTO core.v21_padre VALUES ('p1', 'org_x'), ('p2', 'org_y')",
                # h1 es coherente; h2 (org_x) cuelga de p2 (org_y).
                "INSERT INTO core.v21_hija VALUES ('h1', 'org_x', 'p1'), ('h2', 'org_x', 'p2')",
            ):
                await sesion.execute(text(sql))

            filas = await db.todos(
                sesion,
                "SELECT fila->>'id' AS id, tenant_hija, tenant_padre "
                "FROM core.incoherencias_tenant() WHERE tabla_hija = 'core.v21_hija'",
            )
        finally:
            await tx.rollback()

    assert filas == [{"id": "h2", "tenant_hija": "org_x", "tenant_padre": "org_y"}]
