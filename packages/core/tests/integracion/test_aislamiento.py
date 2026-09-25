# tests/integracion/test_aislamiento.py

import pytest
from kallicode_core import db
from sqlalchemy import text

TENANT_A = "org_tenant_a"
TENANT_B = "org_tenant_b"


# Tablas que la API consulta filtrando por tenant_id directo.
# core.organizations es la excepción: ahí el tenant es la propia fila
# (columna id, no tenant_id), así que se trata aparte.
API_TENANT_TABLES = (
    "core.subscriptions",
    "core.users",
    "core.invitations",
    "core.refresh_tokens",
    "core.password_reset_tokens",
    "core.production_lines",
    "core.ticket_counters",
    "core.tickets",
    "core.ticket_attachments",
    "core.ticket_watchers",
    "core.specs",
    "core.spec_alternatives",
    "core.spec_criteria",
    "core.jobs",
    "core.job_transitions",
    "core.llm_steps",
    "core.gate_signatures",
)


@pytest.fixture
async def tenant_a_registrado():
    """Crea la organización del tenant A con sesión administrativa
    (BYPASSRLS) y la elimina al terminar. Sin esto, las pruebas de
    aislamiento pueden 'pasar' solo porque la tabla está vacía, no
    porque RLS funcione."""
    async with db.sesion_sistema() as admin:
        await admin.execute(
            text(
                "INSERT INTO core.organizations (id, nombre) "
                "VALUES (:id, :nombre) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": TENANT_A, "nombre": "Organización de prueba A"},
        )

    yield


@pytest.mark.integracion
@pytest.mark.asyncio
async def test_app_sin_tenant_no_ve_filas() -> None:
    """Sesión de aplicación sin haber llamado nunca a
    set_config('app.tenant_id', ...): debe comportarse como si no
    hubiera ningún dato. A propósito NO se fija tenant_id a '' ni a
    ningún otro valor — se simula el caso real de una sesión que
    nunca pasó por sesion_tenant()."""
    maker = await db._maker_app()
    async with maker() as session, session.begin():
        tickets = await session.execute(text("SELECT count(*) FROM core.tickets"))
        jobs = await session.execute(text("SELECT count(*) FROM core.jobs"))
        audit_events = await session.execute(text("SELECT count(*) FROM audit.audit_events"))

        assert tickets.scalar_one() == 0
        assert jobs.scalar_one() == 0
        assert audit_events.scalar_one() == 0


@pytest.mark.integracion
@pytest.mark.asyncio
async def test_rol_app_no_tiene_bypassrls() -> None:
    maker = await db._maker_app()
    async with maker() as session, session.begin():
        result = await session.execute(
            text(
                """
                SELECT
                    current_user,
                    rolsuper,
                    rolbypassrls
                FROM pg_roles
                WHERE rolname = current_user
                """
            )
        )

        username, is_superuser, has_bypassrls = result.one()

        assert username == "kallicode_app"
        assert is_superuser is False
        assert has_bypassrls is False


@pytest.mark.integracion
@pytest.mark.asyncio
async def test_tenant_b_no_ve_filas_de_tenant_a(tenant_a_registrado) -> None:
    async with db.sesion_tenant(TENANT_B) as session:
        result = await session.execute(
            text("SELECT count(*) FROM core.organizations WHERE id = :tenant_id"),
            {"tenant_id": TENANT_A},
        )
        assert result.scalar_one() == 0, "El tenant B puede ver la organización del tenant A"

        for table in API_TENANT_TABLES:
            result = await session.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id = :tenant_id"),
                {"tenant_id": TENANT_A},
            )
            assert result.scalar_one() == 0, f"El tenant B puede ver filas del tenant A en {table}"


@pytest.mark.integracion
@pytest.mark.asyncio
async def test_tenant_a_ve_su_organizacion_y_tenant_b_no(
    tenant_a_registrado,
) -> None:
    """Verifica ambos lados: que RLS no bloquea al dueño de los datos
    (falso positivo típico si la política está mal escrita) y que sí
    bloquea al resto."""
    async with db.sesion_tenant(TENANT_A) as session_a:
        result_a = await session_a.execute(
            text("SELECT count(*) FROM core.organizations WHERE id = :tenant_id"),
            {"tenant_id": TENANT_A},
        )
        assert result_a.scalar_one() == 1, (
            "El tenant A no puede ver su propia organización: "
            "la política RLS es demasiado restrictiva"
        )

    async with db.sesion_tenant(TENANT_B) as session_b:
        result_b = await session_b.execute(
            text("SELECT count(*) FROM core.organizations WHERE id = :tenant_id"),
            {"tenant_id": TENANT_A},
        )
        assert result_b.scalar_one() == 0
