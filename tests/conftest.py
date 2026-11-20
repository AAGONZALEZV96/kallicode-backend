"""Fixtures del contrato transversal (KC-BE-07-3), con PostgreSQL real."""

from __future__ import annotations

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
        for clave, rol in (("admin_id", "admin"), ("user_id_destino", "member")):
            await db.execute(
                text("""INSERT INTO core.users (id, tenant_id, email, nombre, rol)
                        VALUES (:i, :t, :email, :nombre, :rol)"""),
                {
                    "i": datos[clave],
                    "t": datos["tenant_id"],
                    "email": f"{clave}@{sufijo}.test",
                    "nombre": clave,
                    "rol": rol,
                },
            )
        await db.execute(
            text("""INSERT INTO core.connections
                        (id, tenant_id, nombre, proveedor, categoria, estado)
                    VALUES (:i, :t, 'Conexión Test', 'github', 'repositorio', 'conectada')"""),
            {"i": datos["connection_id"], "t": datos["tenant_id"]},
        )

    try:
        yield datos
    finally:
        async with sesion_sistema() as db:
            params = {"t": datos["tenant_id"]}
            # Mismo procedimiento de limpieza append-only que apps/api/tests/conftest.py.
            # Todo queda en una transacción: también se restaura el trigger si hay rollback.
            await db.execute(
                text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable")
            )
            await db.execute(text("DELETE FROM audit.audit_events WHERE tenant_id=:t"), params)
            await db.execute(
                text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable")
            )
            for tabla in ("audit.chain_heads", "core.connections", "core.users"):
                await db.execute(text(f"DELETE FROM {tabla} WHERE tenant_id=:t"), params)
            await db.execute(text("DELETE FROM core.organizations WHERE id=:t"), params)


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
    """Simula la identidad; conserva autorización, SQL y auditoría reales."""
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


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as cliente:
        yield cliente
