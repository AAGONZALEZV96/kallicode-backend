"""Pruebas de aislamiento multi-tenant mediante PostgreSQL RLS."""

from __future__ import annotations

from uuid import uuid4

import pytest
from kallicode_core.db import (
    get_maker_app,
    sesion_sistema,
    sesion_tenant,
)
from sqlalchemy import text


@pytest.fixture(autouse=True)
async def reset_db_engines():
    """Asegura motores limpios entre pruebas."""
    import kallicode_core.db as db_module

    yield

    if db_module._engine_app is not None:
        await db_module._engine_app.dispose()

    if db_module._engine_admin is not None:
        await db_module._engine_admin.dispose()

    db_module._engine_app = None
    db_module._maker_app = None
    db_module._engine_admin = None
    db_module._maker_admin = None


async def preparar_datos():
    """
    Crea un escenario aislado con dos tenants.

    Se utilizan IDs únicos para que las pruebas sean repetibles
    sin depender de datos existentes en la base de datos.
    """

    sufijo = uuid4().hex[:12]

    tenant_a = f"test-a-{sufijo}"
    tenant_b = f"test-b-{sufijo}"

    ticket_a = f"ticket-a-{sufijo}"
    ticket_b = f"ticket-b-{sufijo}"

    job_a = f"job-a-{sufijo}"
    job_b = f"job-b-{sufijo}"

    async with sesion_sistema() as db:
        await db.execute(
            text(
                """
                INSERT INTO core.organizations (id, nombre)
                VALUES
                    (:tenant_a, 'Tenant A'),
                    (:tenant_b, 'Tenant B')
                """
            ),
            {
                "tenant_a": tenant_a,
                "tenant_b": tenant_b,
            },
        )

        await db.execute(
            text(
                """
                INSERT INTO core.tickets (
                    id,
                    tenant_id,
                    tipo,
                    titulo,
                    descripcion
                )
                VALUES
                    (
                        :ticket_a,
                        :tenant_a,
                        'bug',
                        'Ticket Tenant A',
                        'Ticket perteneciente al tenant A'
                    ),
                    (
                        :ticket_b,
                        :tenant_b,
                        'bug',
                        'Ticket Tenant B',
                        'Ticket perteneciente al tenant B'
                    )
                """
            ),
            {
                "ticket_a": ticket_a,
                "tenant_a": tenant_a,
                "ticket_b": ticket_b,
                "tenant_b": tenant_b,
            },
        )

        await db.execute(
            text(
                """
                INSERT INTO core.jobs (
                    id,
                    tenant_id,
                    ticket_id,
                    estado,
                    prioridad
                )
                VALUES
                    (
                        :job_a,
                        :tenant_a,
                        :ticket_a,
                        'pendiente_asignacion',
                        'P2'
                    ),
                    (
                        :job_b,
                        :tenant_b,
                        :ticket_b,
                        'pendiente_asignacion',
                        'P2'
                    )
                """
            ),
            {
                "job_a": job_a,
                "tenant_a": tenant_a,
                "ticket_a": ticket_a,
                "job_b": job_b,
                "tenant_b": tenant_b,
                "ticket_b": ticket_b,
            },
        )

        await db.execute(
            text(
                """
                INSERT INTO audit.audit_events (
                    tenant_id,
                    ticket_id,
                    job_id,
                    actor_tipo,
                    actor_id,
                    evento,
                    resumen,
                    datos
                )
                VALUES
                    (
                        :tenant_a,
                        :ticket_a,
                        :job_a,
                        'sistema',
                        'test',
                        'test_event_a',
                        'Evento de prueba tenant A',
                        '{"test": true}'
                    ),
                    (
                        :tenant_b,
                        :ticket_b,
                        :job_b,
                        'sistema',
                        'test',
                        'test_event_b',
                        'Evento de prueba tenant B',
                        '{"test": true}'
                    )
                """
            ),
            {
                "tenant_a": tenant_a,
                "ticket_a": ticket_a,
                "job_a": job_a,
                "tenant_b": tenant_b,
                "ticket_b": ticket_b,
                "job_b": job_b,
            },
        )

    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "ticket_a": ticket_a,
        "ticket_b": ticket_b,
        "job_a": job_a,
        "job_b": job_b,
    }


@pytest.mark.asyncio
async def test_app_session_sin_tenant_devuelve_cero_filas():
    """
    Una sesión de aplicación sin app.tenant_id no debe poder
    consultar filas de ningún tenant mediante RLS.
    """

    datos = await preparar_datos()

    maker = get_maker_app()

    async with maker() as db:
        usuario = await db.execute(text("SELECT current_user"))
        current_user = usuario.scalar_one()

        assert current_user == "kallicode_app"

        tenant_id = await db.execute(
            text("SELECT current_setting('app.tenant_id', true)")
        )

        assert tenant_id.scalar_one() in ("", None)

        tickets = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM core.tickets
                WHERE id IN (:ticket_a, :ticket_b)
                """
            ),
            {
                "ticket_a": datos["ticket_a"],
                "ticket_b": datos["ticket_b"],
            },
        )

        jobs = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM core.jobs
                WHERE id IN (:job_a, :job_b)
                """
            ),
            {
                "job_a": datos["job_a"],
                "job_b": datos["job_b"],
            },
        )

        audit_events = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM audit.audit_events
                WHERE tenant_id IN (:tenant_a, :tenant_b)
                """
            ),
            {
                "tenant_a": datos["tenant_a"],
                "tenant_b": datos["tenant_b"],
            },
        )

        assert tickets.scalar_one() == 0
        assert jobs.scalar_one() == 0
        assert audit_events.scalar_one() == 0


@pytest.mark.asyncio
async def test_aislamiento_cruzado_entre_tenants_y_bypassrls():
    """
    El tenant B solamente debe poder observar sus propias filas.

    Además, la sesión de aplicación debe ejecutarse con un rol
    que NO tenga BYPASSRLS.
    """

    datos = await preparar_datos()

    async with sesion_tenant(datos["tenant_b"], actor="test") as db:
        usuario = await db.execute(text("SELECT current_user"))
        current_user = usuario.scalar_one()

        assert current_user == "kallicode_app"

        bypass = await db.execute(
            text(
                """
                SELECT rolbypassrls
                FROM pg_roles
                WHERE rolname = current_user
                """
            )
        )

        rol_bypass_rls = bypass.scalar_one()

        assert rol_bypass_rls is False

        ticket_a = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM core.tickets
                WHERE id = :ticket_a
                """
            ),
            {"ticket_a": datos["ticket_a"]},
        )

        ticket_b = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM core.tickets
                WHERE id = :ticket_b
                """
            ),
            {"ticket_b": datos["ticket_b"]},
        )

        job_a = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM core.jobs
                WHERE id = :job_a
                """
            ),
            {"job_a": datos["job_a"]},
        )

        job_b = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM core.jobs
                WHERE id = :job_b
                """
            ),
            {"job_b": datos["job_b"]},
        )

        audit_a = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM audit.audit_events
                WHERE tenant_id = :tenant_a
                """
            ),
            {"tenant_a": datos["tenant_a"]},
        )

        audit_b = await db.execute(
            text(
                """
                SELECT COUNT(*)
                FROM audit.audit_events
                WHERE tenant_id = :tenant_b
                """
            ),
            {"tenant_b": datos["tenant_b"]},
        )

        assert ticket_a.scalar_one() == 0
        assert ticket_b.scalar_one() == 1

        assert job_a.scalar_one() == 0
        assert job_b.scalar_one() == 1

        assert audit_a.scalar_one() == 0
        assert audit_b.scalar_one() == 1