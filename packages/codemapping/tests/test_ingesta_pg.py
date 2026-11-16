"""Ciclo del snapshot y ANALYZE por fase contra PostgreSQL real (KC-CM-11-1).

Las pruebas con mocks de `SnapshotManager` no veían que la base rechaza los estados 'iniciado' y 'vigente'
(CHECK snapshots_estado_chk) ni que `SET LOCAL ... = %s` no admite parámetros. Aquí corre contra la base.
Requiere las migraciones (hasta 062) y KC_DATABASE_URL_MIGRATIONS (superusuario). Usa un tenant propio y lo
borra al terminar.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from kallicode_codemapping.graph_loader import GraphLoader, normalizar_dsn
from kallicode_codemapping.snapshots import SnapshotManager

pytestmark = pytest.mark.integracion

TENANT = "t_ingesta_pg"
SHA = "a" * 40


@pytest.fixture
def dsn_admin() -> Iterator[str]:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS (superusuario) no definida")
    dsn = normalizar_dsn(url)
    try:
        with psycopg.connect(dsn) as c:
            if not c.execute("SELECT to_regproc('cm.analizar_tablas')").fetchone()[0]:  # type: ignore[index]
                pytest.skip("falta la migración 062")
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    with psycopg.connect(dsn) as c:
        c.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (TENANT, TENANT),
        )
    yield dsn
    with psycopg.connect(dsn) as c:
        c.execute("DELETE FROM cm.repo_snapshots WHERE tenant_id = %s", (TENANT,))
        c.execute("DELETE FROM core.organizations WHERE id = %s", (TENANT,))


def _dsn_app(admin: str) -> str:
    return admin + ("&" if "?" in admin else "?") + "options=-c%20role%3Dkallicode_app"


def test_ciclo_del_snapshot_con_estados_validos(dsn_admin: str) -> None:
    mgr = SnapshotManager(_dsn_app(dsn_admin), TENANT)
    snap, estado, fase = mgr.obtener_o_crear_snapshot(SHA, "main")
    assert (estado, fase) == ("iniciado", 0)
    assert mgr.obtener_checkpoint(snap) == 0

    mgr.registrar_checkpoint(snap, 3, {"archivos": 1})
    assert mgr.obtener_checkpoint(snap) == 3

    # Reanuda el mismo snapshot en lugar de crear otro.
    snap2, _, fase2 = mgr.obtener_o_crear_snapshot(SHA, "main")
    assert (snap2, fase2) == (snap, 3)

    mgr.marcar_completado(snap, {"nodos": 1})
    with psycopg.connect(dsn_admin) as c:
        assert c.execute(
            "SELECT estado FROM cm.repo_snapshots WHERE id = %s", (snap,)
        ).fetchone() == ("completo",)


def test_analizar_tablas_como_app_y_lista_blanca(dsn_admin: str) -> None:
    with psycopg.connect(_dsn_app(dsn_admin)) as c:
        filas = c.execute(
            "SELECT tabla, duracion_ms FROM cm.analizar_tablas(%s)", (["edges", "symbols"],)
        ).fetchall()
        assert [f[0] for f in filas] == ["edges", "symbols"]
        assert all(f[1] >= 0 for f in filas)
        c.rollback()
        for malo in (["users"], ["edges; DROP TABLE cm.files"], [None]):
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute("SELECT * FROM cm.analizar_tablas(%s)", (malo,))
            c.rollback()


def test_analizar_tablas_no_se_concede_a_public(dsn_admin: str) -> None:
    with psycopg.connect(dsn_admin) as c:
        assert c.execute(
            "SELECT has_function_privilege('public', 'cm.analizar_tablas(text[])', 'EXECUTE')"
        ).fetchone() == (False,)


def test_el_cargador_analiza_y_no_falla_si_la_funcion_no_existe(dsn_admin: str) -> None:
    loader = GraphLoader(dsn=_dsn_app(dsn_admin), tenant_id=TENANT)
    with loader.fase("analisis") as cur:
        loader._analizar(cur, ["edges"])
        assert loader._fase_tablas == set()
        # Una tabla fuera de la lista blanca falla en la función, pero la fase se confirma igual.
        loader._analizar(cur, ["users"])
        assert cur.execute("SELECT 1").fetchone() == (1,)
