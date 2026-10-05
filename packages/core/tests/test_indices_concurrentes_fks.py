import os

import psycopg
import pytest

FK_INDEX_CHECKS = [
    ("audit_exports", ["tenant_id", "solicitado_por"]),
    ("audit_exports", ["tenant_id", "ticket_id"]),
    ("escalations", ["tenant_id", "job_id"]),
    ("gate_signatures", ["tenant_id", "actor_id"]),
    ("gate_signatures", ["tenant_id", "spec_id"]),
    ("gate_signatures", ["tenant_id", "ticket_id"]),
    ("invitations", ["tenant_id", "invitado_por"]),
    ("job_transitions", ["tenant_id", "job_id"]),
    ("jobs", ["tenant_id", "ticket_id"]),
    ("llm_steps", ["tenant_id", "job_id"]),
    ("llm_steps", ["tenant_id", "ticket_id"]),
    ("model_assignments", ["tenant_id", "fallback_proveedor"]),
    ("model_assignments", ["tenant_id", "principal_proveedor"]),
    ("notifications", ["tenant_id", "ticket_id"]),
    ("notifications", ["tenant_id", "user_id"]),
    ("onboarding_documents", ["tenant_id", "subido_por"]),
    ("password_reset_tokens", ["tenant_id", "user_id"]),
    ("production_lines", ["tenant_id", "job_id"]),
    ("qa_results", ["tenant_id", "qa_run_id"]),
    ("qa_runs", ["tenant_id", "spec_id"]),
    ("qa_runs", ["tenant_id", "ticket_id"]),
    ("refresh_tokens", ["tenant_id", "user_id"]),
    ("security_findings", ["tenant_id", "ticket_id"]),
    ("spec_alternatives", ["tenant_id", "spec_id"]),
    ("spec_criteria", ["tenant_id", "spec_id"]),
    ("specs", ["tenant_id", "ticket_id"]),
    ("ticket_attachments", ["tenant_id", "subido_por"]),
    ("ticket_attachments", ["tenant_id", "ticket_id"]),
    ("ticket_watchers", ["tenant_id", "ticket_id"]),
    ("ticket_watchers", ["tenant_id", "user_id"]),
    ("tickets", ["tenant_id", "cancelado_por"]),
    ("tickets", ["tenant_id", "duplicado_de"]),
    ("tickets", ["tenant_id", "reportado_por"]),
    ("upgrade_requests", ["tenant_id", "solicitado_por"]),
    ("usage_loc", ["tenant_id", "ticket_id"]),
    ("usage_tokens", ["tenant_id", "ticket_id"]),
    ("webhook_inbox", ["tenant_id", "connection_id"]),
    ("webhook_inbox", ["tenant_id", "ticket_id"]),
]


def normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


def test_indices_en_fks_compuestas():
    raw_url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@localhost:5432/kallicode_test"
    )
    db_url = normalizar_dsn(raw_url)

    try:
        conn = psycopg.connect(db_url)
    except Exception as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")

    query = """
    SELECT
        t.relname AS tabla,
        array_agg(a.attname ORDER BY k.n) AS columnas
    FROM pg_index i
    JOIN pg_class c ON c.oid = i.indexrelid
    JOIN pg_class t ON t.oid = i.indrelid
    JOIN pg_namespace n ON n.oid = t.relnamespace
    JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, n) ON true
    JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
    WHERE n.nspname = 'core'
      AND i.indisvalid
    GROUP BY t.relname, c.relname;
    """

    with conn, conn.cursor() as cur:
        cur.execute(query)
        indices = cur.fetchall()

    indices_existentes = set()
    for tabla, columnas in indices:
        if len(columnas) >= 2:
            indices_existentes.add((tabla, tuple(columnas[:2])))

    for tabla, cols in FK_INDEX_CHECKS:
        clave = (tabla, tuple(cols))
        assert clave in indices_existentes, (
            f"Falta índice sobre core.{tabla} para las columnas {cols}"
        )
