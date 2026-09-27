import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from kallicode_api.deps import UsuarioActual, usuario_actual
from kallicode_api.main import app
from kallicode_core.db import sesion_sistema


@pytest_asyncio.fixture
async def db_admin():
    """Sesión con BYPASSRLS, solo para sembrar datos de prueba."""
    async with sesion_sistema() as db:
        yield db
        await db.rollback()  # cada test queda limpio, sin importar si hizo commit parcial


@pytest_asyncio.fixture
async def seed_dos_tenants():
    """Crea organizaciones, las commitea y las borra al finalizar (aislamiento real)."""
    import uuid

    from sqlalchemy import text

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
            text("DELETE FROM core.tickets WHERE tenant_id IN (:a, :b)"), {"a": id_a, "b": id_b}
        )
        # LÍNEA NUEVA: Limpiamos los contadores automáticos para evitar el ForeignKeyViolationError
        await db.execute(
            text("DELETE FROM core.ticket_counters WHERE tenant_id IN (:a, :b)"),
            {"a": id_a, "b": id_b},
        )
        await db.execute(
            text("DELETE FROM core.organizations WHERE id IN (:a, :b)"), {"a": id_a, "b": id_b}
        )
        await db.commit()


@pytest_asyncio.fixture
async def crear_ticket():
    """Factory: crea un ticket y hace commit explícito."""
    import uuid

    from sqlalchemy import text

    async def _crear(tenant_id: str, etapa: str = "triage", titulo: str = "Ticket de prueba"):
        ticket_id = str(uuid.uuid4())

        async with sesion_sistema() as db:
            resultado = await db.execute(
                text("""INSERT INTO core.tickets (id, tenant_id, titulo, descripcion, tipo, prioridad, etapa, creado_en)
                         VALUES (:id, :t, :titulo, 'Descripción de prueba', 'bug', 'media', :etapa, now())
                         RETURNING numero, tenant_id, etapa"""),
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


def auth_headers(tenant_id: str):
    """
    Sobreescribe la dependencia usuario_actual para simular un usuario
    autenticado de ese tenant, sin pasar por login/JWT real.
    """
    app.dependency_overrides[usuario_actual] = lambda: UsuarioActual(
        user_id="usuario-test",
        tenant_id=tenant_id,
        rol="admin",
        restringido=False,
    )
    return {}


@pytest_asyncio.fixture(autouse=True)
async def limpiar_overrides():
    yield
    app.dependency_overrides.clear()
