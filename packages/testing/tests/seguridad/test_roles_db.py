"""
Ticket: KC-QA-B2-02
Suite de seguridad para verificación de superusuario y bypass de RLS.
Garantiza que la API y los workers operen bajo RLS estricto y cierra el hallazgo QA-F01.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Habilitar el soporte asíncrono para todas las pruebas del módulo
pytestmark = pytest.mark.asyncio


async def test_kallicode_roles_no_bypassrls(db_session: AsyncSession) -> None:
    """
    Verifica que los roles operacionales (kallicode_app, kallicode_worker)
    existan en el motor de base de datos y no posean privilegios de superusuario
    ni el atributo BYPASSRLS, el cual rompería el aislamiento de tenants.
    """
    query = text(
        "SELECT rolname, rolsuper, rolbypassrls "
        "FROM pg_roles "
        "WHERE rolname IN ('kallicode_app', 'kallicode_worker')"
    )
    result = await db_session.execute(query)
    roles = result.fetchall()

    assert len(roles) > 0, (
        "No se encontraron los roles operacionales (kallicode_app, kallicode_worker) en pg_roles."
    )

    for rolname, rolsuper, rolbypassrls in roles:
        assert not rolsuper, (
            f"El rol operacional {rolname} es superusuario (riesgo crítico de seguridad)."
        )
        assert not rolbypassrls, (
            f"El rol operacional {rolname} tiene BYPASSRLS habilitado (falla el aislamiento RLS)."
        )


async def test_no_superuser_active_connections(db_session: AsyncSession) -> None:
    """
    Falla si detecta conexiones activas en la base de datos actual utilizando la cuenta 'postgres'
    u otra cuenta superusuario para ejecutar consultas operacionales.
    """
    query = text(
        "SELECT pa.usename, pa.application_name, pa.state "
        "FROM pg_stat_activity pa "
        "JOIN pg_roles pr ON pa.usename = pr.rolname "
        "WHERE pa.datname = current_database() "
        "AND pr.rolsuper = true "
        "AND pa.pid <> pg_backend_pid() "
        "AND pa.state = 'active'"
    )
    result = await db_session.execute(query)
    active_superuser_connections = result.fetchall()

    assert len(active_superuser_connections) == 0, (
        f"Se detectaron conexiones operacionales activas utilizando superusuario: "
        f"{active_superuser_connections}"
    )


async def test_current_session_is_operational_role(db_session: AsyncSession) -> None:
    """
    Confirma que el cliente asíncrono actual inyectado por conftest.py está conectado
    con un rol estricto y sometido a las políticas RLS del esquema.
    """
    query = text(
        "SELECT pr.rolname, pr.rolsuper, pr.rolbypassrls "
        "FROM pg_roles pr "
        "WHERE pr.rolname = current_user"
    )
    result = await db_session.execute(query)
    current_role = result.fetchone()

    assert current_role is not None
    rolname, rolsuper, rolbypassrls = current_role

    assert not rolsuper, f"La conexión actual de la prueba ({rolname}) opera como superusuario."
    assert not rolbypassrls, (
        f"La conexión actual de la prueba ({rolname}) opera con el atributo BYPASSRLS."
    )
