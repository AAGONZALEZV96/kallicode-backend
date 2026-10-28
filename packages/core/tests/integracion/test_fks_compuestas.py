"""FK compuestas con tenant_id (KC-DB-03-2, cierra DB-F01).

Requiere db/migrations/013..016 aplicadas. La base debe RECHAZAR que una fila de
un tenant cuelgue de un padre de otro tenant.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from kallicode_core import db
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integracion

# FK entre dos tablas con tenant_id, con indicación de si incluyen tenant_id.
_FKS_CON_TENANT = """
SELECT c.conname, c.conrelid::regclass::text AS hija, c.convalidated,
       EXISTS (SELECT 1 FROM unnest(c.conkey) AS k(n)
                 JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n
                WHERE a.attname = 'tenant_id') AS compuesta
  FROM pg_constraint c
 WHERE c.contype = 'f'
   AND EXISTS (SELECT 1 FROM pg_attribute
                WHERE attrelid = c.conrelid AND attname = 'tenant_id' AND NOT attisdropped)
   AND EXISTS (SELECT 1 FROM pg_attribute
                WHERE attrelid = c.confrelid AND attname = 'tenant_id' AND NOT attisdropped)
"""


async def _fks() -> list[dict]:
    async with db.sesion_sistema() as admin:
        return await db.todos(admin, _FKS_CON_TENANT)


async def test_no_queda_ninguna_fk_escalar_entre_tablas_con_tenant() -> None:
    escalares = [f"{f['hija']}.{f['conname']}" for f in await _fks() if not f["compuesta"]]
    assert escalares == [], f"FK que aún permiten colgar filas de otro tenant: {escalares}"


async def test_todas_las_compuestas_estan_validadas_y_con_su_nombre() -> None:
    fks = await _fks()
    assert len(fks) >= 47
    assert [f["conname"] for f in fks if not f["convalidated"]] == []
    assert [f["conname"] for f in fks if f["conname"].endswith("_t")] == []


async def test_not_valid_y_validate_quedan_en_el_ledger() -> None:
    async with db.sesion_sistema() as admin:
        archivos = await db.todos(
            admin,
            "SELECT archivo FROM core.schema_migrations WHERE archivo IN "
            "('014_fks_compuestas_not_valid.sql', '015_fks_compuestas_validate.sql', "
            "'016_fks_retirar_escalares.sql') ORDER BY 1",
        )
    assert len(archivos) == 3, archivos


@pytest.fixture
async def ticket_de_b() -> AsyncIterator[tuple[str, str, str]]:
    sufijo = uuid.uuid4().hex[:10]
    tenant_a, tenant_b, ticket_b = f"org_fka_{sufijo}", f"org_fkb_{sufijo}", f"tk_fk_{sufijo}"
    async with db.sesion_sistema() as admin:
        for t in (tenant_a, tenant_b):
            await admin.execute(
                text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org prueba FK')"),
                {"t": t},
            )
        await admin.execute(
            text(
                "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion) "
                "VALUES (:id, :t, 'bug', 'Ticket prueba FK', 'KC-DB-03-2')"
            ),
            {"id": ticket_b, "t": tenant_b},
        )
    try:
        yield tenant_a, tenant_b, ticket_b
    finally:
        async with db.sesion_sistema() as admin:
            for tabla in ("core.tickets", "core.ticket_counters"):
                await admin.execute(
                    text(f"DELETE FROM {tabla} WHERE tenant_id IN (:a, :b)"),
                    {"a": tenant_a, "b": tenant_b},
                )
            await admin.execute(
                text("DELETE FROM core.organizations WHERE id IN (:a, :b)"),
                {"a": tenant_a, "b": tenant_b},
            )


async def test_una_spec_no_puede_colgar_de_un_ticket_de_otro_tenant(ticket_de_b) -> None:
    """Antes de KC-DB-03-2 este INSERT se aceptaba en silencio (DB-F01)."""
    tenant_a, _, ticket_b = ticket_de_b
    with pytest.raises(IntegrityError, match="specs_ticket_id_fkey"):
        async with db.sesion_sistema() as admin:
            await admin.execute(
                text(
                    "INSERT INTO core.specs (id, tenant_id, ticket_id, version, spec_md_ref, "
                    "scope_paths) VALUES (:id, :a, :tk, 1, 'blob/spec.md', '{}')"
                ),
                {"id": f"sp_fk_{uuid.uuid4().hex[:10]}", "a": tenant_a, "tk": ticket_b},
            )


async def test_borrar_un_ticket_vacia_solo_ticket_id_en_usage_loc(ticket_de_b) -> None:
    """ON DELETE SET NULL (ticket_id): en una FK compuesta, un SET NULL a secas
    vaciaría también tenant_id, que es NOT NULL. Todo se deshace al final."""
    _, tenant_b, ticket_b = ticket_de_b
    maker = await db._maker_admin()
    async with maker() as admin:
        tx = await admin.begin()
        try:
            await admin.execute(
                text(
                    "INSERT INTO core.usage_loc (tenant_id, ticket_id, ciclo, tipo, netas, "
                    "motivo_ajuste) VALUES (:t, :tk, '2026-10', 'ajuste', 0, 'prueba FK')"
                ),
                {"t": tenant_b, "tk": ticket_b},
            )
            await admin.execute(text("DELETE FROM core.tickets WHERE id = :tk"), {"tk": ticket_b})
            fila = await db.uno(
                admin,
                "SELECT tenant_id, ticket_id FROM core.usage_loc WHERE tenant_id = :t",
                {"t": tenant_b},
            )
        finally:
            await tx.rollback()

    assert fila == {"tenant_id": tenant_b, "ticket_id": None}


async def test_lines_job_fk_sigue_siendo_diferida() -> None:
    async with db.sesion_sistema() as admin:
        fila = await db.uno(
            admin,
            "SELECT condeferrable, condeferred FROM pg_constraint WHERE conname = 'lines_job_fk'",
        )
    assert fila == {"condeferrable": True, "condeferred": True}
