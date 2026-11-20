"""Congelación del esquema v2 (KC-DB-12-1).

Criterios de aceptación:
  2. Etiqueta de versión en core.schema_migrations, expuesta por core.version_esquema()
     (test_version_esquema_..., test_el_trigger_..., test_kallicode_app_...). El smoke post-deploy la
     verifica con db/tools/smoke_post_deploy.sql.
  3. El esquema aplicado se compara con la instantánea congelada db/esquema_v2.snapshot.json y la prueba falla
     ante cualquier divergencia no declarada como migración (test_el_esquema_aplicado_coincide_...,
     test_una_divergencia_no_declarada_...).

Las pruebas de deriva hacen DDL como kallicode_admin dentro de una transacción que SIEMPRE se revierte.
Si la prueba de coincidencia falla tras añadir una migración, regenera la instantánea:
    python db/tools/congelar_esquema.py
"""

from __future__ import annotations

import importlib.util
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row

RAIZ = Path(__file__).resolve().parents[4]
HERRAMIENTA = RAIZ / "db" / "tools" / "congelar_esquema.py"
REGENERAR = "Regenera la instantánea con: python db/tools/congelar_esquema.py"


def _cargar_herramienta() -> ModuleType:
    spec = importlib.util.spec_from_file_location("congelar_esquema", HERRAMIENTA)
    assert spec and spec.loader
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


ce = _cargar_herramienta()


@pytest.fixture(scope="module")
def instantanea() -> dict[str, Any]:
    datos: dict[str, Any] = ce.cargar()
    return datos


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _conectar(variable: str) -> psycopg.Connection[dict[str, Any]]:
    url = os.getenv(variable)
    if not url:
        pytest.skip(f"{variable} no definida")
    try:
        return psycopg.connect(_normalizar_dsn(url), autocommit=False, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    c = _conectar("KC_DATABASE_URL_MIGRATIONS")
    try:
        fila = c.execute("SELECT to_regproc('core.version_esquema') AS f").fetchone()
        if not fila or fila["f"] is None:
            pytest.skip("migración 067 sin aplicar")
        # Como las migraciones: el DDL de las pruebas lo hace el dueño del esquema.
        c.execute("SET LOCAL ROLE kallicode_admin")
        yield c
    finally:
        c.rollback()  # nada de lo que haga una prueba persiste
        c.close()


# ----------------------------------------------------------------------------
# Instantánea committeada (sin base de datos)
# ----------------------------------------------------------------------------
def test_la_instantanea_recoge_todas_las_migraciones_del_repo(instantanea: dict[str, Any]) -> None:
    diferencias = ce.comparar_migraciones(instantanea["migraciones"], ce.migraciones_del_repo())
    assert not diferencias, "\n".join([*diferencias, REGENERAR])


def test_la_instantanea_es_la_v2_y_tiene_formato_conocido(instantanea: dict[str, Any]) -> None:
    assert instantanea["version_formato"] == ce.VERSION_FORMATO
    assert instantanea["etiqueta"].startswith("v2.")
    assert set(instantanea["catalogo"]) == set(ce.CONSULTAS)
    # Ninguna partición hija ni nada de retencion_archivo por dentro.
    for clave in instantanea["catalogo"]["tablas"]:
        assert not clave.startswith("retencion_archivo."), clave
        assert "_p20" not in clave and not clave.endswith("_default"), clave


def test_la_instantanea_esta_serializada_de_forma_canonica(instantanea: dict[str, Any]) -> None:
    """Regenerarla sin cambios no produce diff: claves ordenadas, LF, salto final."""
    assert ce.INSTANTANEA.read_bytes().decode("utf-8") == ce.serializar(instantanea)


# ----------------------------------------------------------------------------
# Comparación (unitarias)
# ----------------------------------------------------------------------------
def test_comparar_catalogos_informa_altas_bajas_y_cambios() -> None:
    antes = {"tablas": {"core.a": {"rls": True}, "core.b": {"rls": True}}}
    despues = {
        "tablas": {"core.a": {"rls": False}, "core.c": {"rls": True}},
        "indices": {"core.i": 1},
    }
    assert ce.comparar_catalogos(antes, despues) == [
        "+ indices: core.i = 1",
        '~ tablas: core.a: {"rls": true} -> {"rls": false}',
        "- tablas: core.b",
        '+ tablas: core.c = {"rls": true}',
    ]
    assert ce.comparar_catalogos(antes, antes) == []


def test_comparar_migraciones_informa_nuevas_retiradas_y_editadas() -> None:
    congeladas = {"001_a.sql": "x", "002_b.sql": "y"}
    declaradas = {"001_a.sql": "x", "002_b.sql": "z", "003_c.sql": "w"}
    assert ce.comparar_migraciones(congeladas, declaradas) == [
        "~ 002_b.sql: sha256 distinto al de la instantánea",
        "+ 003_c.sql: migración nueva que la instantánea no recoge",
    ]
    assert ce.comparar_migraciones({"9_x.sql": "a"}, {}) == [
        "- 9_x.sql: está en la instantánea pero no en db/migrations"
    ]


def test_el_cuerpo_de_una_funcion_no_depende_del_fin_de_linea() -> None:
    lf = ce._normalizar("funciones", {"cuerpo": "BEGIN\n  RETURN 1;\nEND"})
    crlf = ce._normalizar("funciones", {"cuerpo": "BEGIN\r\n  RETURN 1;\r\nEND"})
    assert lf == crlf and set(lf) == {"cuerpo_sha256"}
    assert ce._normalizar("tablas", {"rls": True}) == {"rls": True}


def test_la_cli_exige_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KC_DATABASE_URL_ADMIN", raising=False)
    monkeypatch.delenv("KC_DATABASE_URL_MIGRATIONS", raising=False)
    with pytest.raises(SystemExit):
        ce.main([])


# ----------------------------------------------------------------------------
# Criterio 3: el esquema aplicado coincide con la instantánea
# ----------------------------------------------------------------------------
@pytest.mark.integracion
def test_la_base_tiene_aplicadas_las_migraciones_de_la_instantanea(
    conn: psycopg.Connection[dict[str, Any]], instantanea: dict[str, Any]
) -> None:
    diferencias = ce.comparar_migraciones(instantanea["migraciones"], ce.leer_ledger(conn))
    assert not diferencias, "\n".join(diferencias)


@pytest.mark.integracion
def test_el_esquema_aplicado_coincide_con_la_instantanea(
    conn: psycopg.Connection[dict[str, Any]], instantanea: dict[str, Any]
) -> None:
    diferencias = ce.comparar_catalogos(instantanea["catalogo"], ce.extraer_catalogo(conn))
    assert not diferencias, (
        "El esquema aplicado diverge de db/esquema_v2.snapshot.json. Todo cambio de esquema llega como "
        "migración (docs/politica-cambios-esquema.md) y regenera la instantánea en el mismo PR.\n  "
        + "\n  ".join(diferencias)
    )


@pytest.mark.integracion
def test_la_cli_comprueba_la_base_contra_la_instantanea(capsys: pytest.CaptureFixture[str]) -> None:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    assert ce.main(["--dsn", _normalizar_dsn(url), "--comprobar"]) == 0
    assert "coincide" in capsys.readouterr().out


SUFIJO = uuid.uuid4().hex[:8]
DERIVAS = [
    pytest.param(
        "ALTER TABLE core.jobs ADD COLUMN kc_deriva text",
        "+ columnas: core.jobs.kc_deriva",
        id="columna-nueva",
    ),
    pytest.param(
        "ALTER TABLE core.jobs ALTER COLUMN iteraciones DROP NOT NULL",
        "~ columnas: core.jobs.iteraciones",
        id="nulabilidad",
    ),
    pytest.param(
        f"CREATE INDEX kc_deriva_{SUFIJO} ON core.jobs (creado_en)",
        f"+ indices: core.kc_deriva_{SUFIJO}",
        id="indice-nuevo",
    ),
    pytest.param(
        "ALTER TABLE core.jobs NO FORCE ROW LEVEL SECURITY",
        "~ tablas: core.jobs",
        id="rls-force",
    ),
    pytest.param(
        "DROP POLICY tenant_isolation ON core.jobs",
        "- politicas: core.jobs.tenant_isolation",
        id="politica-retirada",
    ),
    pytest.param(
        "GRANT TRUNCATE ON core.jobs TO kallicode_app",
        "~ tablas: core.jobs",
        id="privilegio",
    ),
    pytest.param(
        "CREATE OR REPLACE FUNCTION core.version_esquema() RETURNS TABLE (etiqueta text, "
        "archivo_etiqueta text, etiquetada_en timestamptz, ultima_migracion text, "
        "migraciones_aplicadas integer, migraciones_posteriores integer) LANGUAGE sql STABLE "
        "SECURITY DEFINER SET search_path = pg_catalog, core AS "
        "$$ SELECT 'v9.9.9', NULL::text, NULL::timestamptz, NULL::text, 0, 0 $$",
        "~ funciones: core.version_esquema()",
        id="cuerpo-de-funcion",
    ),
    pytest.param(
        f"CREATE TABLE cm.kc_deriva_{SUFIJO} (id int)",
        f"+ tablas: cm.kc_deriva_{SUFIJO}",
        id="tabla-nueva",
    ),
]


@pytest.mark.integracion
@pytest.mark.parametrize(("ddl", "esperado"), DERIVAS)
def test_una_divergencia_no_declarada_hace_fallar_la_comparacion(
    conn: psycopg.Connection[dict[str, Any]], instantanea: dict[str, Any], ddl: str, esperado: str
) -> None:
    """DDL hecho a mano (sin migración): la comparación lo detecta y lo nombra."""
    conn.execute(ddl)  # type: ignore[arg-type]
    diferencias = ce.comparar_catalogos(instantanea["catalogo"], ce.extraer_catalogo(conn))
    assert any(d.startswith(esperado) for d in diferencias), diferencias


@pytest.mark.integracion
def test_las_particiones_hijas_no_cuentan_como_divergencia(
    conn: psycopg.Connection[dict[str, Any]], instantanea: dict[str, Any]
) -> None:
    """pg_partman y la partición por tenant crean hijas con el tiempo: no son cambios de esquema."""
    conn.execute(
        f"CREATE TABLE vec.doc_chunks_kc_{SUFIJO} PARTITION OF vec.doc_chunks "  # type: ignore[arg-type]
        f"FOR VALUES IN ('{uuid.uuid4()}')"
    )
    conn.execute(
        f"CREATE TABLE core.job_transitions_kc_{SUFIJO} PARTITION OF core.job_transitions "  # type: ignore[arg-type]
        "FOR VALUES FROM ('2199-01-01') TO ('2199-02-01')"
    )
    assert ce.comparar_catalogos(instantanea["catalogo"], ce.extraer_catalogo(conn)) == []


# ----------------------------------------------------------------------------
# Criterio 2: etiqueta de versión y core.version_esquema()
# ----------------------------------------------------------------------------
def _version(conn: psycopg.Connection[dict[str, Any]]) -> dict[str, Any]:
    fila = conn.execute("SELECT * FROM core.version_esquema()").fetchone()
    assert fila is not None
    return fila


@pytest.mark.integracion
def test_version_esquema_devuelve_la_etiqueta_congelada(
    conn: psycopg.Connection[dict[str, Any]], instantanea: dict[str, Any]
) -> None:
    v = _version(conn)
    assert v["etiqueta"] == instantanea["etiqueta"] == "v2.0.0"
    assert v["archivo_etiqueta"] == "067_version_esquema.sql"
    assert v["ultima_migracion"] == max(instantanea["migraciones"])
    assert v["migraciones_aplicadas"] == len(instantanea["migraciones"])
    fila = conn.execute(
        "SELECT etiqueta_version FROM core.schema_migrations WHERE archivo = '067_version_esquema.sql'"
    ).fetchone()
    assert fila == {"etiqueta_version": "v2.0.0"}


def _registrar(conn: psycopg.Connection[dict[str, Any]], archivo: str) -> None:
    conn.execute(
        "INSERT INTO core.schema_migrations (archivo, sha256, duracion_ms) VALUES (%s, %s, 0)",
        (archivo, "0" * 64),
    )


@pytest.mark.integracion
def test_el_trigger_etiqueta_la_fila_con_el_guc_y_lo_consume(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    """Lo que hará una futura migración que fije v2.1.0: set_config local y el runner inserta su fila."""
    conn.execute("SELECT set_config('kc.etiqueta_version', 'v2.1.0', true)")
    _registrar(conn, "998_prueba_etiqueta.sql")
    v = _version(conn)
    assert (v["etiqueta"], v["archivo_etiqueta"], v["migraciones_posteriores"]) == (
        "v2.1.0",
        "998_prueba_etiqueta.sql",
        0,
    )
    # Consumido: la siguiente fila de la transacción no hereda la etiqueta.
    assert conn.execute("SELECT current_setting('kc.etiqueta_version', true) AS g").fetchone() == {
        "g": ""
    }
    _registrar(conn, "999_prueba_sin_etiqueta.sql")
    v = _version(conn)
    assert (v["etiqueta"], v["ultima_migracion"], v["migraciones_posteriores"]) == (
        "v2.1.0",
        "999_prueba_sin_etiqueta.sql",
        1,
    )


@pytest.mark.integracion
def test_una_migracion_sin_etiqueta_cuenta_como_posterior(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    # Las migraciones reales posteriores a la 067 (p. ej. 068, solo COMMENT) ya cuentan como posteriores.
    antes = _version(conn)["migraciones_posteriores"]
    _registrar(conn, "999_prueba_sin_etiqueta.sql")
    v = _version(conn)
    assert (v["etiqueta"], v["migraciones_posteriores"]) == ("v2.0.0", antes + 1)


@pytest.mark.integracion
@pytest.mark.parametrize("etiqueta", ["2.1.0", "v2.1", "v2.1.0-rc1", "V2.1.0"])
def test_una_etiqueta_mal_formada_se_rechaza(
    conn: psycopg.Connection[dict[str, Any]], etiqueta: str
) -> None:
    conn.execute("SELECT set_config('kc.etiqueta_version', %s, true)", (etiqueta,))
    with pytest.raises(errors.CheckViolation):
        _registrar(conn, "998_prueba_etiqueta.sql")


@pytest.mark.integracion
def test_una_etiqueta_no_se_repite(conn: psycopg.Connection[dict[str, Any]]) -> None:
    conn.execute("SELECT set_config('kc.etiqueta_version', 'v2.0.0', true)")
    with pytest.raises(errors.UniqueViolation):
        _registrar(conn, "998_prueba_etiqueta.sql")


@pytest.mark.integracion
def test_kallicode_app_lee_la_version_pero_no_el_ledger() -> None:
    c = _conectar("KC_DATABASE_URL")
    try:
        if c.execute("SELECT to_regproc('core.version_esquema') AS f").fetchone() == {"f": None}:
            pytest.skip("migración 067 sin aplicar")
        assert _version(c)["etiqueta"] == "v2.0.0"
        with pytest.raises(errors.InsufficientPrivilege):
            c.execute("SELECT count(*) FROM core.schema_migrations")
    finally:
        c.rollback()
        c.close()
