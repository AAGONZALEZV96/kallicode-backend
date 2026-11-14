import os
import uuid

import psycopg
import pytest


def _conn_admin() -> psycopg.Connection:
    url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@127.0.0.1:5432/kallicode_test"
    )
    try:
        return psycopg.connect(url, autocommit=True)
    except (psycopg.OperationalError, psycopg.ProgrammingError, psycopg.Error) as exc:
        pytest.skip(f"Base de datos no accesible en este entorno: {exc}")


def test_catalogos_roles_y_etapas() -> None:
    tenant_id = f"org_dom_{uuid.uuid4().hex[:10]}"
    user_id = f"usr_{uuid.uuid4().hex[:10]}"

    with _conn_admin() as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """
                INSERT INTO core.organizations (id, nombre, idioma, timezone, despliegue, fabrica_activa)
                VALUES (%s, 'Org Dominios', 'es', 'UTC', 'cloud', true)
                """,
                (tenant_id,),
            )

            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                cur.execute(
                    """
                    INSERT INTO core.users (id, tenant_id, email, nombre, rol, estado)
                    VALUES (%s, %s, %s, 'User Bad', 'rol_inexistente', 'activo')
                    """,
                    (user_id, tenant_id, f"{user_id}@example.com"),
                )

            cur.execute(
                """
                INSERT INTO core.users (id, tenant_id, email, nombre, rol, estado)
                VALUES (%s, %s, %s, 'User Good', 'architect', 'activo')
                """,
                (user_id, tenant_id, f"{user_id}@example.com"),
            )

            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                cur.execute(
                    """
                    INSERT INTO core.tickets (
                        id, tenant_id, numero, titulo, descripcion, tipo, prioridad, etapa, origen, reportado_por
                    )
                    VALUES (%s, %s, 1, 'Tk', 'Desc', 'bug', 'alta', 'etapa_inventada', 'portal', %s)
                    """,
                    (f"tk_{uuid.uuid4().hex[:8]}", tenant_id, user_id),
                )
        finally:
            cur.execute("SELECT core.suprimir_tenant(%s)", (tenant_id,))
