"""Pruebas de validación de autovacuum y fillfactor en tablas de alta rotación.

Ticket KC-DB-10-5.
"""

import os
from typing import Any

import psycopg
import pytest

TABLAS_ESPERADAS: dict[tuple[str, str], dict[str, str]] = {
    ("core", "jobs"): {
        "autovacuum_vacuum_scale_factor": "0.02",
        "autovacuum_analyze_scale_factor": "0.01",
        "fillfactor": "80",
    },
    ("core", "production_lines"): {
        "autovacuum_vacuum_scale_factor": "0.05",
        "autovacuum_analyze_scale_factor": "0.02",
        "fillfactor": "85",
    },
    ("cm", "curation_queue"): {
        "autovacuum_vacuum_scale_factor": "0.02",
        "autovacuum_analyze_scale_factor": "0.01",
        "fillfactor": "80",
    },
    ("core", "idempotency_keys"): {
        "autovacuum_vacuum_scale_factor": "0.02",
        "autovacuum_analyze_scale_factor": "0.01",
        "fillfactor": "85",
    },
}


def normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


def obtener_conexion() -> psycopg.Connection[Any]:
    raw_url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@localhost:5432/kallicode_test"
    )
    return psycopg.connect(normalizar_dsn(raw_url), autocommit=True)


def test_reloptions_autovacuum_en_pg_class() -> None:
    """Verifica que las 4 tablas tengan configurados los parámetros esperados en pg_class.reloptions."""
    try:
        conn = obtener_conexion()
    except Exception as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")

    query = """
        SELECT n.nspname, c.relname, c.reloptions
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE (n.nspname = 'core' AND c.relname IN ('jobs', 'production_lines', 'idempotency_keys'))
           OR (n.nspname = 'cm' AND c.relname = 'curation_queue');
    """

    with conn, conn.cursor() as cur:
        cur.execute(query)
        rows = {(r[0], r[1]): r[2] or [] for r in cur.fetchall()}

    assert len(rows) == 4, f"Se esperaban 4 tablas, se encontraron {len(rows)}"

    for tabla, esperados in TABLAS_ESPERADAS.items():
        opciones_raw = rows.get(tabla)
        assert opciones_raw is not None, f"Falta la tabla {tabla[0]}.{tabla[1]}"
        opciones_dict = dict(opt.split("=", 1) for opt in opciones_raw if "=" in opt)

        for clave, valor_esperado in esperados.items():
            assert opciones_dict.get(clave) == valor_esperado, (
                f"{tabla[0]}.{tabla[1]}: {clave} esperado={valor_esperado}, actual={opciones_dict.get(clave)}"
            )


def test_estadisticas_y_limpieza_dead_tuples_bajo_carga() -> None:
    """Simula carga de alta rotación en core.idempotency_keys y valida limpieza de dead tuples y pg_stats."""
    try:
        conn = obtener_conexion()
    except Exception as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")

    tenant_id = "org_test_autovacuum_10_5"

    with conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.organizations (id, nombre)
            VALUES (%s, 'Test Autovacuum 10-5')
            ON CONFLICT (id) DO NOTHING;
            """,
            (tenant_id,),
        )
        cur.execute("SELECT set_config('app.current_tenant', %s, false);", (tenant_id,))

        cur.execute(
            """
            INSERT INTO core.idempotency_keys (clave, tenant_id, endpoint, respuesta, status_code)
            SELECT
                'key_' || g,
                %s,
                '/internal/v1/test',
                '{"ok": true}'::jsonb,
                200
            FROM generate_series(1, 500) AS g
            ON CONFLICT DO NOTHING;
            """,
            (tenant_id,),
        )

        cur.execute(
            """
            UPDATE core.idempotency_keys
            SET status_code = 201
            WHERE tenant_id = %s;
            """,
            (tenant_id,),
        )

        # Ejecutar VACUUM ANALYZE con filas presentes para poblar pg_stats y limpiar los dead tuples del UPDATE
        cur.execute("VACUUM ANALYZE core.idempotency_keys;")

        cur.execute(
            """
            SELECT n_dead_tup, last_analyze
            FROM pg_stat_all_tables
            WHERE schemaname = 'core' AND relname = 'idempotency_keys';
            """
        )
        row_stat = cur.fetchone()
        assert row_stat is not None
        assert row_stat[0] == 0, (
            f"Se esperaban 0 dead tuples tras VACUUM ANALYZE, hay {row_stat[0]}"
        )
        assert row_stat[1] is not None, "last_analyze debe quedar registrado tras ANALYZE"

        cur.execute(
            """
            SELECT count(*)
            FROM pg_stats
            WHERE schemaname = 'core' AND tablename = 'idempotency_keys';
            """
        )
        stat_count = cur.fetchone()[0]
        assert stat_count > 0, (
            "pg_stats debe contener estadísticas actualizadas para core.idempotency_keys"
        )

        # Limpieza final y recuperación de espacio tras DELETE
        cur.execute(
            """
            DELETE FROM core.idempotency_keys
            WHERE tenant_id = %s;
            """,
            (tenant_id,),
        )
        cur.execute("VACUUM ANALYZE core.idempotency_keys;")
        cur.execute("SELECT set_config('app.current_tenant', '', false);")
        cur.execute("DELETE FROM core.organizations WHERE id = %s;", (tenant_id,))
