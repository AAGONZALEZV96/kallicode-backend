import os
from datetime import date

import psycopg
import pytest


def normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


@pytest.fixture
def db_conn():
    raw_url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@localhost:5432/kallicode_test"
    )
    db_url = normalizar_dsn(raw_url)
    try:
        conn = psycopg.connect(db_url, autocommit=True)
    except Exception as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    yield conn
    conn.close()


def test_obtener_ciclo_cuota_por_aniversario(db_conn):
    tenant_id = "org_test_aniversario_054"

    with db_conn.cursor() as cur:
        # Asegurar organización
        cur.execute(
            """
            INSERT INTO core.organizations (id, nombre)
            VALUES (%s, 'Org Test Aniversario')
            ON CONFLICT (id) DO NOTHING;
            """,
            (tenant_id,),
        )

        # Obtener un plan_codigo válido existente en la base de datos
        cur.execute("SELECT codigo FROM core.plans LIMIT 1;")
        fila_plan = cur.fetchone()
        if fila_plan:
            plan_codigo = fila_plan[0]
        else:
            cur.execute(
                """
                INSERT INTO core.plans (codigo, nombre)
                VALUES ('plan_test', 'Plan Test')
                ON CONFLICT (codigo) DO NOTHING;
                """
            )
            plan_codigo = "plan_test"

        # Caso 1: Suscripción con inicio el 15 de enero de 2026
        cur.execute("DELETE FROM core.subscriptions WHERE tenant_id = %s;", (tenant_id,))
        cur.execute(
            """
            INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, estado, inicia_el, renueva_el)
            VALUES ('sub_aniv_01', %s, %s, 'activa', '2026-01-15', '2026-02-15');
            """,
            (tenant_id, plan_codigo),
        )

        # Consulta al 20 de enero (después del aniversario -> ciclo 2026-01-15)
        cur.execute(
            "SELECT core.obtener_ciclo_cuota_actual(%s, %s::date);",
            (tenant_id, date(2026, 1, 20)),
        )
        assert cur.fetchone()[0] == "2026-01-15"

        # Consulta al 10 de febrero (antes del día 15 -> sigue en ciclo 2026-01-15)
        cur.execute(
            "SELECT core.obtener_ciclo_cuota_actual(%s, %s::date);",
            (tenant_id, date(2026, 2, 10)),
        )
        assert cur.fetchone()[0] == "2026-01-15"

        # Consulta al 15 de febrero (día de renovación -> nuevo ciclo 2026-02-15)
        cur.execute(
            "SELECT core.obtener_ciclo_cuota_actual(%s, %s::date);",
            (tenant_id, date(2026, 2, 15)),
        )
        assert cur.fetchone()[0] == "2026-02-15"

        # Caso 2: Suscripción en día 31 (caso borde para febrero)
        cur.execute(
            """
            UPDATE core.subscriptions
            SET inicia_el = '2026-01-31', renueva_el = '2026-02-28'
            WHERE id = 'sub_aniv_01';
            """
        )
        # En febrero de 2026 (mes de 28 días), el ciclo debe ajustar al 28 de febrero
        cur.execute(
            "SELECT core.obtener_ciclo_cuota_actual(%s, %s::date);",
            (tenant_id, date(2026, 2, 28)),
        )
        assert cur.fetchone()[0] == "2026-02-28"

        # Limpieza
        cur.execute("DELETE FROM core.subscriptions WHERE tenant_id = %s;", (tenant_id,))
        cur.execute("DELETE FROM core.organizations WHERE id = %s;", (tenant_id,))
