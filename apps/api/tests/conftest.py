import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.db import sesion_sistema
from sqlalchemy import text

ROLES = ("owner", "admin", "architect", "approver", "member", "viewer")


@pytest_asyncio.fixture
async def db_admin():
    """Sesión con BYPASSRLS, solo para sembrar datos de prueba."""
    async with sesion_sistema() as db:
        yield db
        await db.rollback()  # cada test queda limpio, sin importar si hizo commit parcial


@pytest_asyncio.fixture
async def seed_dos_tenants():
    """Crea organizaciones, las commitea y las borra al finalizar (aislamiento real)."""
    id_a = str(uuid.uuid4())
    id_b = str(uuid.uuid4())

    # 1. SETUP: Creamos y commiteamos para que la API lo pueda ver
    async with sesion_sistema() as db:
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, 'Tenant A Test')"),
            {"id": id_a},
        )
        await db.execute(
            text("INSERT INTO core.organizations (id, nombre) VALUES (:id, 'Tenant B Test')"),
            {"id": id_b},
        )
        await db.commit()

    yield id_a, id_b  # Entregamos el control al test

    # 2. TEARDOWN: Borramos la evidencia al terminar el test
    async with sesion_sistema() as db:
        await db.execute(
            text("DELETE FROM core.tickets WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        # Limpiamos los contadores automáticos para evitar el ForeignKeyViolationError
        await db.execute(
            text("DELETE FROM core.ticket_counters WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        # audit_events es append-only (trigger); se desactiva solo en la limpieza de tests
        await db.execute(text("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_immutable"))
        await db.execute(
            text("DELETE FROM audit.audit_events WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(text("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_immutable"))
        # La cabeza solo se puede borrar cuando el tenant ya no tiene eventos (KC-DB-05-1)
        await db.execute(
            text("DELETE FROM audit.chain_heads WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(
            text("DELETE FROM core.organizations WHERE id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.commit()


@pytest_asyncio.fixture
async def usuarios_por_rol(seed_dos_tenants):
    """Un usuario real por cada uno de los seis roles, en cada uno de los dos tenants.

    Devuelve {tenant_id: {rol: user_id}}. Ambos tenants tienen exactamente los
    mismos roles (datos equivalentes). Se borran al terminar el test.
    """
    id_a, id_b = seed_dos_tenants
    resultado: dict[str, dict[str, str]] = {id_a: {}, id_b: {}}

    async with sesion_sistema() as db:
        for tenant_id in (id_a, id_b):
            for rol in ROLES:
                user_id = f"u-{rol}-{tenant_id[:8]}"
                await db.execute(
                    text(
                        """INSERT INTO core.users (id, tenant_id, email, nombre, rol)
                           VALUES (:id, :t, :email, :nombre, :rol)"""
                    ),
                    {
                        "id": user_id,
                        "t": tenant_id,
                        "email": f"{rol}@{tenant_id[:8]}.test",
                        "nombre": f"Usuario {rol}",
                        "rol": rol,
                    },
                )
                resultado[tenant_id][rol] = user_id
        await db.commit()

    yield resultado

    async with sesion_sistema() as db:
        await db.execute(
            text("DELETE FROM core.users WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.commit()


@pytest_asyncio.fixture
async def crear_ticket():
    """Factory: crea un ticket y hace commit explícito."""

    async def _crear(tenant_id: str, etapa: str = "triage", titulo: str = "Ticket de prueba"):
        ticket_id = str(uuid.uuid4())

        async with sesion_sistema() as db:
            resultado = await db.execute(
                text(
                    """INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo, prioridad, etapa, creado_en, cerrado_en)
                         VALUES (:id, :t, :titulo, 'Descripción de prueba', 'bug', 'media', :etapa, now(),
                                 -- tk_cerrado_chk (KC-DB-09-2): una etapa terminal exige cerrado_en
                                 CASE WHEN :etapa IN ('produccion', 'cerrado_duplicado') THEN now() END)
                         RETURNING numero, tenant_id, etapa"""
                ),
                {"id": ticket_id, "t": tenant_id, "titulo": titulo, "etapa": etapa},
            )
            await db.commit()  # Hacemos commit para que la API lo lea
            return resultado.mappings().one()

    return _crear


@pytest_asyncio.fixture
async def client():
    """Cliente HTTP async contra la app, sin levantar un servidor real."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def auth_headers(tenant_id: str, rol: str = "admin"):
    """
    Sobreescribe la dependencia usuario_actual para simular un usuario
    autenticado de ese tenant con un rol especifico, sin pasar por login/JWT real.

    En este caso los roles son los que estan definidos en core.users:
    owner | admin | member | architect | viewer | approver
    """
    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id=f"usuario-{rol}",
        tenant_id=tenant_id,
        rol=rol,
        restringido=False,
    )
    return {}


@pytest_asyncio.fixture(autouse=True)
async def limpiar_overrides():
    yield
    app.dependency_overrides.clear()
