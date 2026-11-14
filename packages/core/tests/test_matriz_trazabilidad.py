import os
import uuid

import psycopg
import pytest


def _admin_url() -> str:
    return (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@127.0.0.1:5432/kallicode_test"
    )


def _conn_admin() -> psycopg.Connection:
    return psycopg.connect(_admin_url(), autocommit=True)


def _conn_app() -> psycopg.Connection:
    app_url = os.getenv("KC_DATABASE_URL_APP")
    if app_url:
        return psycopg.connect(app_url, autocommit=False)
    conn = psycopg.connect(_admin_url(), autocommit=False)
    with conn.cursor() as cur:
        cur.execute("SET ROLE kallicode_app")
    return conn


def test_matriz_trazabilidad_append_only_y_rls() -> None:
    t_a = f"org_ta_{uuid.uuid4().hex[:10]}"
    t_b = f"org_tb_{uuid.uuid4().hex[:10]}"
    usr_a = f"usr_{uuid.uuid4().hex[:10]}"
    tk_a = f"tk_{uuid.uuid4().hex[:10]}"
    spec_a = f"spc_{uuid.uuid4().hex[:10]}"
    qa_a = f"qar_{uuid.uuid4().hex[:10]}"
    ctt_id = f"ctt_{uuid.uuid4().hex[:10]}"

    with _conn_admin() as conn, conn.cursor() as cur:
        try:
            for tid in (t_a, t_b):
                cur.execute(
                    """
                    INSERT INTO core.organizations (id, nombre, idioma, timezone, despliegue, fabrica_activa)
                    VALUES (%s, %s, 'es', 'UTC', 'cloud', true)
                    """,
                    (tid, f"Org {tid}"),
                )

            cur.execute(
                """
                INSERT INTO core.users (id, tenant_id, email, nombre, rol, estado)
                VALUES (%s, %s, %s, 'QA User', 'member', 'activo')
                """,
                (usr_a, t_a, f"{usr_a}@example.com"),
            )
            cur.execute(
                """
                INSERT INTO core.tickets (
                    id, tenant_id, numero, titulo, descripcion, tipo, prioridad, etapa, origen, reportado_por
                )
                VALUES (%s, %s, 10, 'Ticket QA', 'Desc', 'funcionalidad', 'media', 'qa', 'portal', %s)
                """,
                (tk_a, t_a, usr_a),
            )
            cur.execute(
                """
                INSERT INTO core.specs (id, tenant_id, ticket_id, version, spec_md_ref, scope_paths)
                VALUES (%s, %s, %s, 1, 'specs/spc_test.md', ARRAY['packages/core'])
                """,
                (spec_a, t_a, tk_a),
            )
            cur.execute(
                """
                INSERT INTO core.qa_runs (id, tenant_id, ticket_id, spec_id, corrida, regresion)
                VALUES (%s, %s, %s, %s, 1, '{}'::jsonb)
                """,
                (qa_a, t_a, tk_a, spec_a),
            )

            cur.execute(
                """
                INSERT INTO core.criteria_test_traceability (
                    id, tenant_id, spec_id, criterio_id, qa_run_id, test_nodeid, resultado, duracion_ms
                )
                VALUES (%s, %s, %s, 'CA-01', %s, 'tests/test_auth.py::test_login', 'passed', 45)
                """,
                (ctt_id, t_a, spec_a, qa_a),
            )

            with pytest.raises(psycopg.Error):
                cur.execute(
                    "UPDATE core.criteria_test_traceability SET resultado = 'failed' WHERE id = %s",
                    (ctt_id,),
                )
            with pytest.raises(psycopg.Error):
                cur.execute(
                    "DELETE FROM core.criteria_test_traceability WHERE id = %s",
                    (ctt_id,),
                )

            with _conn_app() as app_conn, app_conn.cursor() as app_cur:
                app_cur.execute("SELECT set_config('app.tenant_id', %s, true)", (t_b,))
                app_cur.execute(
                    "SELECT count(*) FROM core.criteria_test_traceability WHERE id = %s",
                    (ctt_id,),
                )
                assert app_cur.fetchone()[0] == 0

                app_cur.execute("SELECT set_config('app.tenant_id', %s, true)", (t_a,))
                app_cur.execute(
                    "SELECT count(*) FROM core.criteria_test_traceability WHERE id = %s",
                    (ctt_id,),
                )
                assert app_cur.fetchone()[0] == 1
        finally:
            for tid in (t_a, t_b):
                cur.execute("SELECT core.suprimir_tenant(%s)", (tid,))
