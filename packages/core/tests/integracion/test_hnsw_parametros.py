"""iterative_scan y presupuesto de ef_search por caso de uso (KC-DB-08-1, DB-F10).

Comprueba la configuración que deja la migración 038 y db/00_roles.sql: los parámetros calibrados por caso
de uso (dedup, diseno), que se aplican con vec.configurar_busqueda() con alcance de TRANSACCIÓN (como un
SET LOCAL) y nunca globalmente ni por sesión, que los roles de la aplicación y del worker traen
hnsw.iterative_scan = relaxed_order y que la aplicación no puede cambiar los parámetros. El recall que
consiguen esos parámetros lo mide packages/core/tests/bench_recall_vectorial.py (CI nocturno).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
MIGRACION = RAIZ / "db" / "migrations" / "038_hnsw_parametros.sql"
ROUTER_TICKETS = RAIZ / "apps" / "api" / "src" / "kallicode_api" / "routers" / "tickets.py"
VALOR_DEFECTO_EF = "40"  # el de pgvector, el que había antes


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _conectar(variable: str) -> psycopg.Connection:
    url = os.getenv(variable)
    if not url:
        pytest.skip(f"{variable} no definida")
    try:
        c = psycopg.connect(_normalizar_dsn(url), autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    c.execute("SELECT '[1,2,3]'::vector")  # carga pgvector: registra los parámetros hnsw.*
    c.commit()
    return c


@pytest.fixture
def admin() -> Iterator[psycopg.Connection]:
    c = _conectar("KC_DATABASE_URL_MIGRATIONS")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def app() -> Iterator[psycopg.Connection]:
    c = _conectar("KC_DATABASE_URL")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _valor(c: psycopg.Connection, parametro: str) -> str:
    fila = c.execute(f"SHOW {parametro}").fetchone()
    assert fila is not None
    return str(fila[0])


# --- la calibración ------------------------------------------------------------------------------------
def test_la_tabla_trae_los_dos_casos_calibrados(admin: psycopg.Connection) -> None:
    filas = {
        r[0]: r[1:]
        for r in admin.execute(
            "SELECT caso, ef_search, max_scan_tuples, recall_objetivo FROM vec.parametros_busqueda"
        ).fetchall()
    }
    assert set(filas) == {"dedup", "diseno"}
    ef_dedup, scan_dedup, objetivo_dedup = filas["dedup"]
    ef_diseno, scan_diseno, objetivo_diseno = filas["diseno"]
    assert (ef_dedup, scan_dedup, float(objetivo_dedup)) == (600, 5000, 0.98)
    assert (ef_diseno, scan_diseno, float(objetivo_diseno)) == (400, 5000, 0.90)
    # Los valores del §9.2 (40 y 100) se calibraron sin la política RLS: ya no bastan.
    assert ef_dedup > 40 and ef_diseno > 100
    assert max(ef_dedup, ef_diseno) <= 1000, "pgvector no admite hnsw.ef_search > 1000"


def test_cada_fila_documenta_su_metrica_y_su_justificacion(admin: psycopg.Connection) -> None:
    for caso, metrica, justificacion in admin.execute(
        "SELECT caso, metrica, justificacion FROM vec.parametros_busqueda"
    ).fetchall():
        assert len(metrica) > 20 and len(justificacion) > 60, caso


# --- se fijan por TRANSACCIÓN, no por sesión ni globalmente ---------------------------------------------
@pytest.mark.parametrize(
    ("caso", "ef", "scan"), [("dedup", "600", "5000"), ("diseno", "400", "5000")]
)
def test_configurar_busqueda_fija_los_parametros_solo_hasta_el_final_de_la_transaccion(
    admin: psycopg.Connection, caso: str, ef: str, scan: str
) -> None:
    antes = (_valor(admin, "hnsw.ef_search"), _valor(admin, "hnsw.max_scan_tuples"))
    assert antes[0] == VALOR_DEFECTO_EF
    admin.execute("SELECT vec.configurar_busqueda(%s)", (caso,))
    assert _valor(admin, "hnsw.ef_search") == ef
    assert _valor(admin, "hnsw.max_scan_tuples") == scan
    assert _valor(admin, "hnsw.iterative_scan") == "relaxed_order"
    admin.commit()
    # Nueva transacción en la MISMA sesión: vuelve a los valores de antes (es un SET LOCAL, no un SET).
    assert (_valor(admin, "hnsw.ef_search"), _valor(admin, "hnsw.max_scan_tuples")) == antes


def test_los_parametros_de_un_caso_no_se_filtran_al_siguiente_en_la_misma_conexion(
    app: psycopg.Connection,
) -> None:
    """Es lo que pasa con un pool: la conexión la reutiliza otra consulta, de otro caso de uso."""
    app.execute("SELECT vec.configurar_busqueda('diseno')")
    assert _valor(app, "hnsw.ef_search") == "400"
    app.commit()
    assert _valor(app, "hnsw.ef_search") == VALOR_DEFECTO_EF
    app.execute("SELECT vec.configurar_busqueda('dedup')")
    assert _valor(app, "hnsw.ef_search") == "600"


def test_un_caso_desconocido_falla_con_un_mensaje_claro(admin: psycopg.Connection) -> None:
    with pytest.raises(errors.InvalidParameterValue, match="caso desconocido"):
        admin.execute("SELECT vec.configurar_busqueda('inventado')")


# --- por rol -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("variable", ["KC_DATABASE_URL", "KC_DATABASE_URL_WORKER"])
def test_los_roles_de_la_aplicacion_y_del_worker_traen_iterative_scan(variable: str) -> None:
    c = _conectar(variable)
    try:
        assert _valor(c, "hnsw.iterative_scan") == "relaxed_order", (
            "falta ALTER ROLE ... SET hnsw.iterative_scan en db/00_roles.sql (ejecutarlo como superusuario)"
        )
        # Y sigue siendo por rol, no global: el superusuario de las pruebas no lo trae.
    finally:
        c.rollback()
        c.close()


def test_el_valor_por_rol_no_es_global(admin: psycopg.Connection) -> None:
    assert _valor(admin, "hnsw.iterative_scan") == "off"


# --- permisos ----------------------------------------------------------------------------------------------
def test_la_aplicacion_lee_los_parametros_pero_no_los_cambia(app: psycopg.Connection) -> None:
    assert app.execute("SELECT count(*) FROM vec.parametros_busqueda").fetchone()[0] == 2
    app.execute("SAVEPOINT s")
    with pytest.raises(errors.InsufficientPrivilege):
        app.execute("UPDATE vec.parametros_busqueda SET ef_search = 1 WHERE caso = 'dedup'")
    app.execute("ROLLBACK TO SAVEPOINT s")
    with pytest.raises(errors.InsufficientPrivilege):
        app.execute("DELETE FROM vec.parametros_busqueda")


def test_el_worker_tambien_puede_configurar_la_busqueda() -> None:
    c = _conectar("KC_DATABASE_URL_WORKER")
    try:
        c.execute("SELECT vec.configurar_busqueda('dedup')")
        assert _valor(c, "hnsw.ef_search") == "600"
    finally:
        c.rollback()
        c.close()


# --- el código de la API usa la configuración ---------------------------------------------------------------
def test_las_busquedas_de_la_api_configuran_la_busqueda_y_reordenan() -> None:
    codigo = ROUTER_TICKETS.read_text(encoding="utf-8")
    assert 'configurar_busqueda(db, "dedup")' in codigo
    assert 'configurar_busqueda(db, "diseno")' in codigo
    # relaxed_order puede devolver los resultados ligeramente desordenados: se reordenan fuera.
    assert codigo.count("AS MATERIALIZED") >= 2
    assert codigo.count("dist + 0") >= 2
    # Ningún SET/SET LOCAL de hnsw.* en el código de la aplicación: lo hace la función de la base.
    assert "SET hnsw" not in codigo and "SET LOCAL hnsw" not in codigo


def test_la_migracion_no_fija_nada_a_nivel_global_ni_de_sesion() -> None:
    sql = MIGRACION.read_text(encoding="utf-8").upper()
    assert "ALTER SYSTEM" not in sql
    assert "ALTER DATABASE" not in sql
    assert "SET_CONFIG('HNSW.EF_SEARCH', P.EF_SEARCH::TEXT, TRUE)" in sql, (
        "debe ser local a la transacción"
    )
