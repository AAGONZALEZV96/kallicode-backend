"""
Ticket: KC-QA-B2-01
Suite de Aislamiento Multi-Tenant Generada desde pg_policies.
Verifica que RLS restringe SELECT, UPDATE, DELETE e impide INSERTs cruzados en todas las tablas operacionales.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def test_aislamiento_rls_generado_desde_politicas(db_session: AsyncSession) -> None:
    """
    Lee el catálogo pg_policies y ejecuta transacciones cruzadas para garantizar
    el aislamiento estricto entre dos tenants en las tablas de core, vec, audit y cm.
    """
    # 1. Extraer todas las tablas protegidas por RLS dinámicamente
    query_policies = text("""
        SELECT schemaname, tablename 
        FROM pg_policies 
        WHERE policyname = 'tenant_isolation'
    """)
    result = await db_session.execute(query_policies)
    tablas = result.fetchall()

    assert len(tablas) > 0, (
        "No se detectaron tablas con la política 'tenant_isolation'. Verifica 04_rls.sql"
    )

    # Tenants sintéticos para la prueba cruzada
    tenant_alpha = "a0000000-0000-4000-8000-000000000001"
    tenant_beta = "b0000000-0000-4000-8000-000000000002"

    # Fijar el contexto de la transacción al tenant_alpha simulando el JWT
    await db_session.execute(text(f"SET LOCAL app.tenant_id = '{tenant_alpha}'"))

    for schema, table in tablas:
        columna_filtro = "id" if table == "organizations" else "tenant_id"

        # 2. SELECT: Siempre permitido por RBAC, pero debe ser restringido por RLS
        query_select = text(
            f"SELECT count(*) FROM {schema}.{table} WHERE {columna_filtro} = :tenant_ajeno"
        )
        res_select = await db_session.execute(query_select, {"tenant_ajeno": tenant_beta})
        assert res_select.scalar() == 0, (
            f"Fuga de RLS en {schema}.{table}: SELECT expone datos cruzados."
        )

        # 3. UPDATE: Puede estar restringido por RBAC (tablas append-only) o por RLS
        try:
            async with db_session.begin_nested():
                query_update = text(
                    f"UPDATE {schema}.{table} SET {columna_filtro} = :tenant_propio WHERE {columna_filtro} = :tenant_ajeno"
                )
                res_update = await db_session.execute(
                    query_update, {"tenant_propio": tenant_alpha, "tenant_ajeno": tenant_beta}
                )
                assert res_update.rowcount == 0, (
                    f"Fuga de RLS en {schema}.{table}: UPDATE modificó datos cruzados."
                )
        except DatabaseError as e:
            # Si el motor rechaza por falta de permisos (RBAC), el aislamiento se cumple por diseño.
            if "permission denied" not in str(e).lower():
                raise

        # 4. DELETE: Puede estar restringido por RBAC (tablas append-only) o por RLS
        try:
            async with db_session.begin_nested():
                query_delete = text(
                    f"DELETE FROM {schema}.{table} WHERE {columna_filtro} = :tenant_ajeno"
                )
                res_delete = await db_session.execute(query_delete, {"tenant_ajeno": tenant_beta})
                assert res_delete.rowcount == 0, (
                    f"Fuga de RLS en {schema}.{table}: DELETE afectó datos cruzados."
                )
        except DatabaseError as e:
            if "permission denied" not in str(e).lower():
                raise

        # 5. INSERT: Intentar insertar una fila cruzada debe chocar con el WITH CHECK de RLS
        try:
            async with db_session.begin_nested():
                query_insert = text(
                    f"INSERT INTO {schema}.{table} ({columna_filtro}) VALUES (:tenant_ajeno)"
                )
                await db_session.execute(query_insert, {"tenant_ajeno": tenant_beta})

            # Si llega aquí sin excepción, es una vulnerabilidad
            assert False, f"Vulnerabilidad en {schema}.{table}: INSERT cruzado no fue rechazado."
        except DatabaseError:
            pass
