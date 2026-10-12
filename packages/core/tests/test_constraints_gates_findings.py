import os

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


def test_constraints_gate_signatures(db_conn):
    tenant_id = "org_test_gates_055"
    sello_valido = "a" * 64

    with db_conn.cursor() as cur:
        # Fixtures base con 'nombre' en users y tipo='bug' en tickets
        cur.execute(
            """
            INSERT INTO core.organizations (id, nombre)
            VALUES (%s, 'Org Test Gates')
            ON CONFLICT (id) DO NOTHING;
            """,
            (tenant_id,),
        )
        cur.execute(
            """
            INSERT INTO core.users (id, tenant_id, nombre, email, password_hash, rol)
            VALUES ('usr_test_055', %s, 'Usuario Test', 'user055@test.com', 'hash', 'admin')
            ON CONFLICT (tenant_id, id) DO NOTHING;
            """,
            (tenant_id,),
        )
        cur.execute(
            """
            INSERT INTO core.tickets (id, tenant_id, numero, titulo, descripcion, tipo, etapa)
            VALUES ('tk_test_055', %s, 955, 'Ticket Test Gates', 'Desc', 'bug', 'qa')
            ON CONFLICT (tenant_id, id) DO NOTHING;
            """,
            (tenant_id,),
        )

        # 1. Aprobado con devolver_a no nulo DEBE fallar
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO core.gate_signatures (id, tenant_id, ticket_id, gate, accion, actor_id, devolver_a, sello)
                VALUES ('gs_bad_1', %s, 'tk_test_055', 1, 'aprobado', 'usr_test_055', 'design', %s);
                """,
                (tenant_id, sello_valido),
            )

        # 2. Cambios pedidos sin devolver_a o sin comentario DEBE fallar
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO core.gate_signatures (id, tenant_id, ticket_id, gate, accion, actor_id, sello)
                VALUES ('gs_bad_2', %s, 'tk_test_055', 1, 'cambios_pedidos', 'usr_test_055', %s);
                """,
                (tenant_id, sello_valido),
            )

        # 3. Sello inválido DEBE fallar
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO core.gate_signatures (id, tenant_id, ticket_id, gate, accion, actor_id, sello)
                VALUES ('gs_bad_3', %s, 'tk_test_055', 1, 'aprobado', 'usr_test_055', 'sello_invalido');
                """,
                (tenant_id,),
            )

        # Limpieza
        cur.execute("DELETE FROM core.tickets WHERE tenant_id = %s;", (tenant_id,))
        cur.execute("DELETE FROM core.users WHERE tenant_id = %s;", (tenant_id,))
        cur.execute("DELETE FROM core.organizations WHERE id = %s;", (tenant_id,))


def test_constraints_security_findings(db_conn):
    tenant_id = "org_test_findings_055"

    with db_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.organizations (id, nombre)
            VALUES (%s, 'Org Test Findings')
            ON CONFLICT (id) DO NOTHING;
            """,
            (tenant_id,),
        )
        cur.execute(
            """
            INSERT INTO core.tickets (id, tenant_id, numero, titulo, descripcion, tipo, etapa)
            VALUES ('tk_test_findings', %s, 956, 'Ticket Test Findings', 'Desc', 'bug', 'qa')
            ON CONFLICT (tenant_id, id) DO NOTHING;
            """,
            (tenant_id,),
        )

        # 1. Línea no positiva (ej. 0 o negativa) DEBE fallar
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO core.security_findings (id, tenant_id, ticket_id, origen, severidad, titulo, veredicto, linea)
                VALUES ('sf_bad_1', %s, 'tk_test_findings', 'sast', 'alta', 'Inyeccion', 'confirmado', 0);
                """,
                (tenant_id,),
            )

        # 2. Título vacío DEBE fallar
        with pytest.raises(psycopg.errors.CheckViolation):
            cur.execute(
                """
                INSERT INTO core.security_findings (id, tenant_id, ticket_id, origen, severidad, titulo, veredicto)
                VALUES ('sf_bad_2', %s, 'tk_test_findings', 'sast', 'alta', '   ', 'confirmado');
                """,
                (tenant_id,),
            )

        # Limpieza
        cur.execute("DELETE FROM core.tickets WHERE tenant_id = %s;", (tenant_id,))
        cur.execute("DELETE FROM core.organizations WHERE id = %s;", (tenant_id,))
