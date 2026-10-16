"""kallicode_worker y las colas cross-tenant (KC-DB-02-2, cierra DB-F11).

El worker debe ver TODOS los tenants en las 5 tablas de cola (por política,
no por BYPASSRLS) y no poder leer ninguna otra tabla. Requiere el esquema con
db/migrations/010_rol_worker.sql aplicada y KC_DATABASE_URL_WORKER.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from kallicode_core import db
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integracion

COLAS = (
    "core.webhook_inbox",
    "core.jobs",
    "core.refresh_tokens",
    "core.ticket_attachments",
    "core.idempotency_keys",
)

# Barrido real de cada worker y los índices que pueden servirlo.
# Adjuntos huérfanos: el planificador solía preferir attachments_ticket_ix
# (Index Cond: ticket_id IS NULL), que dejaba a attachments_huerfanos_ix como redundante. KC-DB-06-2
# retiró attachments_ticket_ix (no empezaba por tenant_id y lo cubre
# idx_ticket_attachments_tenant_ticket_id): el barrido, que no filtra por tenant, usa el parcial.
BARRIDOS = [
    (
        "SELECT id FROM core.webhook_inbox "
        "WHERE estado IN ('pendiente','fallido') ORDER BY recibido_en LIMIT 50",
        ("webhook_inbox_pend_ix",),
    ),
    (
        "SELECT DISTINCT tenant_id FROM core.jobs WHERE estado = 'pendiente_asignacion'",
        ("jobs_pendientes_ix",),
    ),
    (
        "SELECT id FROM core.refresh_tokens WHERE expira_en < now() - interval '7 days'",
        ("refresh_tokens_expira_ix",),
    ),
    (
        "SELECT id FROM core.ticket_attachments "
        "WHERE ticket_id IS NULL AND creado_en < now() - interval '48 hours'",
        ("attachments_huerfanos_ix",),
    ),
    (
        "SELECT clave FROM core.idempotency_keys WHERE creado_en < now() - interval '48 hours'",
        ("idempotency_purga_ix",),
    ),
]

TABLAS_PROHIBIDAS = (
    "core.tickets",
    "core.specs",
    "core.gate_signatures",
    "core.users",
    "audit.audit_events",
)

# Orden de borrado (hijas antes que padres) para limpiar los dos tenants.
_LIMPIEZA = (
    "core.idempotency_keys",
    "core.ticket_attachments",
    "core.webhook_inbox",
    "core.job_transitions",
    "core.jobs",
    "core.ticket_watchers",
    "core.tickets",
    "core.ticket_counters",
    "core.refresh_tokens",
    "core.connections",
    "core.users",
)


async def _sembrar_tenant(admin, tenant: str) -> None:
    """Un usuario, una conexión, un ticket y una fila en cada cola."""
    p = {"t": tenant, "s": tenant.removeprefix("org_")}
    for sql in (
        "INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Org prueba worker')",
        "INSERT INTO core.users (id, tenant_id, email, nombre) "
        "VALUES ('u_' || :s, :t, :s || '@prueba.dev', 'Usuario prueba')",
        "INSERT INTO core.connections (id, tenant_id, categoria, proveedor) "
        "VALUES ('cn_' || :s, :t, 'tickets', 'jira')",
        "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion) "
        "VALUES ('tk_' || :s, :t, 'bug', 'Ticket prueba worker', 'KC-DB-02-2')",
        # Colas:
        "INSERT INTO core.webhook_inbox (tenant_id, connection_id, delivery_id, payload) "
        "VALUES (:t, 'cn_' || :s, 'dl_' || :s, '{}')",
        "INSERT INTO core.jobs (id, tenant_id, ticket_id, prioridad) "
        "VALUES ('job_' || :s, :t, 'tk_' || :s, 'P3')",
        "INSERT INTO core.refresh_tokens (id, tenant_id, user_id, token_hash, expira_en) "
        "VALUES ('rt_' || :s, :t, 'u_' || :s, 'hash_' || :s, now() - interval '30 days')",
        "INSERT INTO core.ticket_attachments (id, tenant_id, nombre_archivo, content_type, "
        "tamano_bytes, blob_ref, creado_en) VALUES ('att_' || :s, :t, 'a.txt', 'text/plain', "
        "1, 'blob/' || :s, now() - interval '3 days')",
        "INSERT INTO core.idempotency_keys (clave, tenant_id, endpoint, respuesta, "
        "status_code, creado_en) VALUES ('k_' || :s, :t, '/prueba', '{}', 200, "
        "now() - interval '3 days')",
    ):
        await admin.execute(text(sql), p)


@pytest.fixture
async def dos_tenants() -> AsyncIterator[list[str]]:
    sufijo = uuid.uuid4().hex[:10]
    tenants = [f"org_wka_{sufijo}", f"org_wkb_{sufijo}"]
    async with db.sesion_sistema() as admin:
        for t in tenants:
            await _sembrar_tenant(admin, t)
    try:
        yield tenants
    finally:
        async with db.sesion_sistema() as admin:
            for tabla in _LIMPIEZA:
                await admin.execute(
                    text(f"DELETE FROM {tabla} WHERE tenant_id = ANY(:ts)"), {"ts": tenants}
                )
            await admin.execute(
                text("DELETE FROM core.organizations WHERE id = ANY(:ts)"), {"ts": tenants}
            )


async def test_worker_no_tiene_bypassrls_ni_superusuario() -> None:
    async with db.sesion_worker() as worker:
        fila = await db.uno(
            worker,
            "SELECT current_user AS rol, rolsuper, rolbypassrls "
            "FROM pg_roles WHERE rolname = current_user",
        )
    assert fila == {"rol": "kallicode_worker", "rolsuper": False, "rolbypassrls": False}


@pytest.mark.parametrize("cola", COLAS)
async def test_worker_ve_la_cola_de_todos_los_tenants(dos_tenants: list[str], cola: str) -> None:
    """Sin app.tenant_id: la política worker_colas le deja ver los dos tenants."""
    async with db.sesion_worker() as worker:
        tenants_vistos = await db.valor(
            worker,
            f"SELECT count(DISTINCT tenant_id) FROM {cola} WHERE tenant_id = ANY(:ts)",
            {"ts": dos_tenants},
        )
    assert tenants_vistos == 2, f"kallicode_worker no ve las filas de ambos tenants en {cola}"


@pytest.mark.parametrize(("consulta", "indices"), BARRIDOS, ids=[i[0] for _, i in BARRIDOS])
async def test_el_barrido_usa_su_indice(
    dos_tenants: list[str], consulta: str, indices: tuple[str, ...]
) -> None:
    async with db.sesion_worker() as worker:
        # Con tablas casi vacías el planificador prefiere seq-scan; lo
        # desactivamos para comprobar que el índice es utilizable bajo RLS.
        await worker.execute(text("SET LOCAL enable_seqscan = off"))
        plan = "\n".join(
            fila[0] for fila in (await worker.execute(text(f"EXPLAIN {consulta}"))).all()
        )
    assert "Seq Scan" not in plan, f"el barrido recorre la tabla entera:\n{plan}"
    assert any(i in plan for i in indices), f"el barrido no usa {indices}:\n{plan}"


@pytest.mark.parametrize("tabla", TABLAS_PROHIBIDAS)
async def test_worker_no_puede_leer_tablas_de_negocio(dos_tenants: list[str], tabla: str) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with db.sesion_worker() as worker:
            await worker.execute(text(f"SELECT count(*) FROM {tabla}"))


async def test_worker_solo_lee_jobs(dos_tenants: list[str]) -> None:
    """Mínimo privilegio: el planificador solo consulta jobs; no puede cambiarlos."""
    with pytest.raises(DBAPIError, match="permission denied"):
        async with db.sesion_worker() as worker:
            await worker.execute(
                text("UPDATE core.jobs SET prioridad = 'P1' WHERE tenant_id = ANY(:ts)"),
                {"ts": dos_tenants},
            )
