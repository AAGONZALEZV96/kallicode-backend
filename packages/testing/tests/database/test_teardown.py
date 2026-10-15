"""
Ticket: KC-QA-B2-09
Verificación de Limpieza de Datos Post-Ejecución (Teardown).
Asegura idempotencia total verificando que el delta de filas insertadas
sea 0 gracias al mecanismo de rollback transaccional (SAVEPOINT).
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

# Habilitar el soporte asíncrono para la suite
pytestmark = pytest.mark.asyncio


async def get_total_row_count(session: AsyncSession) -> int:
    """
    Suma el número total de filas de todas las tablas operacionales
    en los esquemas de la arquitectura a las que el rol de aplicación tiene acceso.
    """
    query_tables = text("""
        SELECT schemaname, tablename
        FROM pg_tables
        WHERE schemaname IN ('core', 'vec', 'audit', 'cm')
          AND tablename NOT IN ('schema_migrations')
    """)
    result = await session.execute(query_tables)
    tables = result.fetchall()

    total_rows = 0
    for schema, table in tables:
        count_query = text(f"SELECT count(*) FROM {schema}.{table}")
        try:
            res = await session.execute(count_query)
            total_rows += res.scalar() or 0
        except ProgrammingError as e:
            # Ignoramos silenciosamente si tropezamos con otra tabla sin permisos RBAC de lectura
            if "permission denied" in str(e).lower():
                await session.rollback()
            else:
                raise

    return total_rows


async def test_teardown_idempotencia_garantiza_delta_cero(db_session: AsyncSession) -> None:
    """
    Test metrológico que mide las filas antes y después de una ejecución simulada.
    Garantiza que la limpieza de datos (teardown) restaure la DB a su estado original.
    """
    # 1. Medición Base (Before)
    conteo_inicial = await get_total_row_count(db_session)

    # 2. Simulación de ejecución de test (mutación de la base de datos)
    nested_transaction = await db_session.begin_nested()

    try:
        # Fijamos un tenant dinámico que coincida EXACTAMENTE con el ID a insertar.
        # Esto es vital para no violar el WITH CHECK de la política RLS de core.organizations.
        nuevo_tenant_id = "f0000000-0000-4000-8000-000000000009"
        await db_session.execute(text(f"SET LOCAL app.tenant_id = '{nuevo_tenant_id}'"))

        # Inyectamos una mutación directa para alterar la línea base global
        await db_session.execute(
            text(
                "INSERT INTO core.organizations (id, nombre) "
                f"VALUES ('{nuevo_tenant_id}', 'Test Teardown Org')"
            )
        )

        # Verificar que la inyección tuvo efecto medible
        conteo_durante = await get_total_row_count(db_session)
        assert conteo_durante > conteo_inicial, (
            "Fallo: La mutación de datos no alteró el recuento global."
        )

    finally:
        # 3. FASE DE TEARDOWN: Rollback manual del SAVEPOINT
        await nested_transaction.rollback()

    # 4. Medición Post-Ejecución (After)
    conteo_final = await get_total_row_count(db_session)

    # 5. Criterio de Aceptación: Delta de filas = 0
    delta = conteo_final - conteo_inicial
    assert delta == 0, (
        f"Violación de Idempotencia: Delta de filas es {delta}. La BD retuvo datos residuales."
    )
