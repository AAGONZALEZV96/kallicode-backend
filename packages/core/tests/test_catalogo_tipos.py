import os

import psycopg
import pytest


def normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    if not sep:
        return url
    return f"{esquema.split('+', 1)[0]}://{resto}"


def test_catalogo_tipos_nodo_y_arista():
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

    with conn, conn.cursor() as cur:
        # Verificar tipos de nodo
        cur.execute("SELECT kind FROM cm.node_types ORDER BY kind;")
        node_kinds = [r[0] for r in cur.fetchall()]
        esperados_nodos = [
            "concept",
            "db_column",
            "db_table",
            "definition",
            "file",
            "symbol",
        ]
        assert node_kinds == esperados_nodos, f"Faltan tipos de nodo en el catálogo: {node_kinds}"

        # Verificar tipos de arista
        cur.execute("SELECT tipo FROM cm.edge_types ORDER BY tipo;")
        edge_types = [r[0] for r in cur.fetchall()]
        esperados_aristas = sorted(
            [
                "DEFINES",
                "CALLS",
                "HAS_COLUMN",
                "REFERENCES",
                "USES_TABLE",
                "REALIZED_BY",
                "RELATES",
                "EXPRESSES",
            ]
        )
        assert edge_types == esperados_aristas, (
            f"Faltan tipos de arista en el catálogo: {edge_types}"
        )

        # Verificar vista consolidada
        cur.execute("SELECT count(*) FROM cm.v_catalogo_grafo;")
        total_vistas = cur.fetchone()[0]
        assert total_vistas == 8, (
            f"La vista debe reportar los 8 tipos de relaciones, reportó {total_vistas}"
        )
