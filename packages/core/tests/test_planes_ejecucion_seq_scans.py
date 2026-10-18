import json
import os
from typing import Any

import psycopg
import pytest


def normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


def contiene_seq_scan(nodo_plan: dict[str, Any]) -> bool:
    """Inspecciona recursivamente el árbol del plan de ejecución buscando Seq Scan."""
    node_type = nodo_plan.get("Node Type", "")
    if "Seq Scan" in node_type:
        return True
    for subplan in nodo_plan.get("Plans", []):
        if contiene_seq_scan(subplan):
            return True
    return False


@pytest.fixture
def conn_db():
    raw_url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@localhost:5432/kallicode_test"
    )
    db_url = normalizar_dsn(raw_url)
    try:
        conn = psycopg.connect(db_url, autocommit=False)
    except Exception as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")

    yield conn
    conn.close()


def test_explain_cero_seq_scans_consultas_grafo_y_core(conn_db):
    """Verifica que las consultas críticas de grafo y multitenancy resuelvan vía Index Scan sin Seq Scan."""
    consultas = [
        # 1. Búsqueda de snapshot por commit y estados (cm.repo_snapshots)
        (
            """
            EXPLAIN (FORMAT JSON)
            SELECT id FROM cm.repo_snapshots
            WHERE tenant_id = 'org_test' AND commit_sha = 'abc1234' AND estado = ANY(ARRAY['en_progreso', 'completo'])
            ORDER BY iniciado_en DESC;
            """,
            "cm.repo_snapshots por commit",
        ),
        # 2. Búsqueda de último snapshot completo (cm.repo_snapshots)
        (
            """
            EXPLAIN (FORMAT JSON)
            SELECT commit_sha FROM cm.repo_snapshots
            WHERE tenant_id = 'org_test' AND estado = 'completo'
            ORDER BY finalizado_en DESC NULLS LAST, iniciado_en DESC;
            """,
            "cm.repo_snapshots ultimo completo",
        ),
        # 3. Filtrado de símbolos por tenant y file_id (cm.symbols)
        (
            """
            EXPLAIN (FORMAT JSON)
            SELECT id, kind, name FROM cm.symbols
            WHERE tenant_id = 'org_test' AND file_id = 'file_123';
            """,
            "cm.symbols por file_id",
        ),
        # 4. Hash e ID de files por tenant y path (cm.files)
        (
            """
            EXPLAIN (FORMAT JSON)
            SELECT id, sha256 FROM cm.files
            WHERE tenant_id = 'org_test' AND path = 'src/index.ts';
            """,
            "cm.files por path",
        ),
        # 5. Recorrido saliente de aristas del grafo (cm.edges)
        (
            """
            EXPLAIN (FORMAT JSON)
            SELECT src_kind, dst_kind, props FROM cm.edges
            WHERE tenant_id = 'org_test' AND src_id = 'sym_1' AND tipo = 'CALLS';
            """,
            "cm.edges recorrido saliente",
        ),
        # 6. Recorrido entrante de aristas del grafo (cm.edges)
        (
            """
            EXPLAIN (FORMAT JSON)
            SELECT src_kind, dst_kind, props FROM cm.edges
            WHERE tenant_id = 'org_test' AND dst_id = 'sym_2' AND tipo = 'DEFINES';
            """,
            "cm.edges recorrido entrante",
        ),
        # 7. Firma de gates por ticket tenant-first (core.gate_signatures)
        (
            """
            EXPLAIN (FORMAT JSON)
            SELECT id, accion, actor_id FROM core.gate_signatures
            WHERE tenant_id = 'org_test' AND ticket_id = 'tk_123' AND gate = 1
            ORDER BY creado_en;
            """,
            "core.gate_signatures tenant-first",
        ),
        # 8. Transiciones de jobs tenant-first (core.job_transitions)
        (
            """
            EXPLAIN (FORMAT JSON)
            SELECT id, de_estado, a_estado FROM core.job_transitions
            WHERE tenant_id = 'org_test' AND job_id = 'job_123'
            ORDER BY creado_en;
            """,
            "core.job_transitions tenant-first",
        ),
    ]

    with conn_db.cursor() as cur:
        # Desactivamos sequential scan en la transacción de prueba para obligar al optimizador a usar índices
        cur.execute("SET LOCAL enable_seqscan = off;")

        for query, descripcion in consultas:
            cur.execute(query)
            resultado = cur.fetchone()
            assert resultado is not None, f"No se obtuvo plan para {descripcion}"

            plan_json = resultado[0]
            if isinstance(plan_json, str):
                plan_json = json.loads(plan_json)

            plan_raiz = plan_json[0]["Plan"]
            tiene_seq_scan = contiene_seq_scan(plan_raiz)

            assert not tiene_seq_scan, (
                f"La consulta '{descripcion}' arrojó un Seq Scan en su plan de ejecución:\n"
                f"{json.dumps(plan_raiz, indent=2)}"
            )

        conn_db.rollback()
