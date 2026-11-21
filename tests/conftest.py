"""Fixtures del contrato transversal (KC-BE-07-3), con PostgreSQL real."""

from __future__ import annotations

import math
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.db import sesion_sistema
from sqlalchemy import text


@pytest_asyncio.fixture
async def datos_contrato():
    """Datos exclusivos de cada prueba; las peticiones usan sesiones RLS reales."""
    sufijo = uuid.uuid4().hex
    datos = {
        "tenant_id": f"org_contrato_{sufijo}",
        "admin_id": f"u_admin_{sufijo}",
        "user_id_destino": f"u_destino_{sufijo}",
        "connection_id": f"cn_{sufijo}",
    }

    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:t, 'Contrato Test')"),
            {"t": datos["tenant_id"]},
        )

        for clave, rol in (
            ("admin_id", "admin"),
            ("user_id_destino", "member"),
        ):
            await db.execute(
                text("""
                    INSERT INTO core.users
                        (id, tenant_id, email, nombre, rol)
                    VALUES
                        (:i, :t, :email, :nombre, :rol)
                """),
                {
                    "i": datos[clave],
                    "t": datos["tenant_id"],
                    "email": f"{clave}@{sufijo}.test",
                    "nombre": clave,
                    "rol": rol,
                },
            )

        await db.execute(
            text("""
                INSERT INTO core.connections
                    (id, tenant_id, nombre, proveedor, categoria, estado)
                VALUES
                    (:i, :t, 'Conexión Test', 'github', 'repositorio', 'conectada')
            """),
            {
                "i": datos["connection_id"],
                "t": datos["tenant_id"],
            },
        )
        await db.execute(
            text("""
                INSERT INTO core.subscriptions
                    (id, tenant_id, plan_codigo, estado, inicia_el, renueva_el)
                VALUES
                    (:i, :t, 'growth', 'activa', current_date, current_date + 30)
            """),
            {
                "i": f"sub_{sufijo}",
                "t": datos["tenant_id"],
            },
        )

    try:
        yield datos
    finally:
        async with sesion_sistema() as db:
            params = {"t": datos["tenant_id"]}

            await db.execute(
                text("DELETE FROM vec.ticket_embeddings WHERE tenant_id = :t"),
                params,
            )
            await db.execute(
                text("DELETE FROM core.jobs WHERE tenant_id = :t"),
                params,
            )
            await db.execute(
                text("DELETE FROM core.ticket_attachments WHERE tenant_id = :t"),
                params,
            )
            await db.execute(
                text(
                    "ALTER TABLE core.ticket_watchers DISABLE TRIGGER trg_watchers_protege_creador"
                )
            )
            await db.execute(
                text("DELETE FROM core.ticket_watchers WHERE tenant_id = :t"),
                params,
            )
            await db.execute(
                text("DELETE FROM core.tickets WHERE tenant_id = :t"),
                params,
            )
            await db.execute(
                text("ALTER TABLE core.ticket_watchers ENABLE TRIGGER trg_watchers_protege_creador")
            )

            await db.execute(
                text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable")
            )
            await db.execute(
                text("DELETE FROM audit.audit_events WHERE tenant_id = :t"),
                params,
            )
            await db.execute(
                text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable")
            )

            for tabla in (
                "audit.chain_heads",
                "core.ticket_counters",
                "core.subscriptions",
                "core.connections",
                "core.users",
            ):
                await db.execute(
                    text(f"DELETE FROM {tabla} WHERE tenant_id = :t"),
                    params,
                )

            await db.execute(
                text("DELETE FROM core.organizations WHERE id = :t"),
                params,
            )


@pytest.fixture
def tenant_id(datos_contrato):
    return datos_contrato["tenant_id"]


@pytest.fixture
def user_id_destino(datos_contrato):
    return datos_contrato["user_id_destino"]


@pytest.fixture
def connection_id(datos_contrato):
    return datos_contrato["connection_id"]


@pytest.fixture
def auth_headers_admin(datos_contrato, redis_doble):
    """Simula la identidad admin conservando autorización, SQL y auditoría reales."""
    anterior = app.dependency_overrides.copy()

    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=datos_contrato["admin_id"],
        tenant_id=datos_contrato["tenant_id"],
        rol="admin",
        restringido=False,
    )

    try:
        yield {}
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anterior)


@pytest.fixture
def auth_headers_member(datos_contrato, redis_doble):
    """Simula la identidad member conservando autorización, SQL y auditoría reales."""
    anterior = app.dependency_overrides.copy()

    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=datos_contrato["user_id_destino"],
        tenant_id=datos_contrato["tenant_id"],
        rol="member",
        restringido=False,
    )

    try:
        yield {}
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(anterior)


class EmbebidoDoble:
    """Doble configurable del servicio de embeddings."""

    def __init__(self):
        self.vector: list[float] | None = None
        self.similitud: float | None = None

    async def __call__(self, texto: str) -> list[float]:
        if self.vector is not None:
            return self.vector

        if self.similitud is not None:
            similitud = max(-1.0, min(1.0, self.similitud))
            componente = math.sqrt(max(0.0, 1.0 - similitud**2))
            return [similitud, componente] + [0.0] * 1022

        return [1.0] + [0.0] * 1023


@pytest.fixture
def monkeypatch_embebido(monkeypatch):
    doble = EmbebidoDoble()

    import kallicode_api.routers.tickets as router_tickets

    monkeypatch.setattr(router_tickets, "_embeber", doble)

    yield doble


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as cliente:
        yield cliente
