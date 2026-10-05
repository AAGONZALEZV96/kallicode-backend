import os
import psycopg


EXPECTED_PKS = {
    ("audit", "audit_events"): ["creado_en", "id"],
    ("core", "job_transitions"): ["creado_en", "id"],
    ("core", "notifications"): ["creada_en", "id"],
    ("core", "usage_tokens"): ["creado_en", "id"],
    ("core", "webhook_inbox"): ["recibido_en", "id"],
}


def test_pk_compuestas_para_particionado():
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql://kallicode_admin:KalliCode_Dev2026!@localhost:5432/kallicode_test",
    )

    query = """
    SELECT 
        n.nspname AS esquema,
        c.relname AS tabla,
        array_agg(a.attname ORDER BY array_position(con.conkey, a.attnum)) AS columnas_pk
    FROM pg_constraint con
    JOIN pg_class c ON c.oid = con.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(con.conkey)
    WHERE con.contype = 'p'
      AND (
        (n.nspname = 'audit' AND c.relname = 'audit_events') OR
        (n.nspname = 'core' AND c.relname IN ('usage_tokens', 'job_transitions', 'webhook_inbox', 'notifications'))
      )
    GROUP BY n.nspname, c.relname;
    """

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()

    actual_pks = {(row[0], row[1]): row[2] for row in rows}

    assert len(actual_pks) == 5, f"Se esperaban 5 tablas, se encontraron: {len(actual_pks)}"

    for (schema, table), expected_cols in EXPECTED_PKS.items():
        assert (schema, table) in actual_pks, f"Falta la PK para {schema}.{table}"
        assert actual_pks[(schema, table)] == expected_cols, (
            f"PK incorrecta en {schema}.{table}. Esperada: {expected_cols}, "
            f"Obtenida: {actual_pks[(schema, table)]}"
        )