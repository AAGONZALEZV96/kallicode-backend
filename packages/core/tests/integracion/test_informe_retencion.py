"""Informe de cumplimiento de retención por tabla (KC-DB-10-4).

Criterios de aceptación:
  1. El informe cubre el 100 % de las tablas declaradas en core.retention_policies (y en core.retencion_dedicada), sin
     omisiones, y toda tabla del esquema aparece: con política declarada o marcada como desviación con su
     justificación (test_cubre_..., test_toda_tabla_...).
  2. Alerta si el dato más antiguo supera la retención declarada en más de 7 días; una desviación deliberada se señala
     (test_una_desviacion_deliberada_..., test_la_tolerancia_..., test_una_particionada_...).
  3. core.validar_integridad() (V26 incluido) devuelve 0 hallazgos tras el ciclo completo (test_tras_el_ciclo_...).
El tiempo se simula con p_ahora. Todo corre como kallicode_admin (sesión del superusuario) en una transacción que
SIEMPRE se revierte.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import errors
from psycopg.rows import dict_row

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
SCRIPT = RAIZ / "db" / "tools" / "informe_retencion.sql"
AHORA = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
ESTADOS = {
    "cumple",
    "en_tolerancia",
    "excede",
    "conservar",
    "sin_politica",
    "no_medible",
    "inexistente",
    "ignorada",
}
UNIVERSO = """
    SELECT n.nspname || '.' || c.relname
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE c.relkind IN ('r', 'p') AND NOT c.relispartition
       AND n.nspname IN ('core', 'audit', 'vec', 'cm')
       AND NOT EXISTS (SELECT 1 FROM pg_depend d
                        WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')
"""


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


@pytest.fixture
def conn() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS (superusuario) no definida")
    try:
        c = psycopg.connect(_normalizar_dsn(url), autocommit=False, row_factory=dict_row)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    try:
        fila = c.execute("SELECT to_regproc('core.reporte_cumplimiento_retencion') AS f").fetchone()
        if not fila or fila["f"] is None:
            pytest.skip("migración 057 sin aplicar")
        c.execute("SET TIME ZONE 'UTC'")
        # Como las migraciones: las tablas de prueba las crea (y es dueño de ellas) kallicode_admin, el rol con el
        # que se ejecuta el informe (SECURITY DEFINER).
        c.execute("SET LOCAL ROLE kallicode_admin")
        yield c
    finally:
        c.rollback()  # nada de lo que haga una prueba persiste
        c.close()


# --- utilidades -------------------------------------------------------------------------------------------------
def _nombre(prefijo: str) -> str:
    return f"zz_{prefijo}_{uuid.uuid4().hex[:8]}"


def _dia(dias: int) -> dt.datetime:
    return AHORA - dt.timedelta(days=dias)


def _informe(
    c: psycopg.Connection[dict[str, Any]],
    ahora: dt.datetime = AHORA,
    tolerancia: str = "7 days",
) -> list[dict[str, Any]]:
    return c.execute(
        "SELECT * FROM core.reporte_cumplimiento_retencion(%s, %s::interval)", (ahora, tolerancia)
    ).fetchall()


def _fila(
    c: psycopg.Connection[dict[str, Any]],
    tabla: str,
    tenant: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    filas = [f for f in _informe(c, **kw) if f["tabla"] == tabla and f["tenant"] == tenant]
    assert len(filas) == 1, filas
    return filas[0]


def _v26(c: psycopg.Connection[dict[str, Any]]) -> int:
    fila = c.execute(
        "SELECT problemas FROM core.validar_integridad() WHERE chequeo = 'V26_tabla_sin_retencion'"
    ).fetchone()
    assert fila is not None
    return int(fila["problemas"])


def _tabla(c: psycopg.Connection[dict[str, Any]], prefijo: str, con_tenant: bool = False) -> str:
    t = f"core.{_nombre(prefijo)}"
    extra = ", tenant_id text" if con_tenant else ""
    c.execute(f"CREATE TABLE {t} (id serial PRIMARY KEY{extra}, creado_en timestamptz NOT NULL)")  # type: ignore[arg-type]
    return t


def _filas(
    c: psycopg.Connection[dict[str, Any]], tabla: str, *dias: int, tenant: str | None = None
) -> None:
    for d in dias:
        if tenant is None:
            c.execute(f"INSERT INTO {tabla} (creado_en) VALUES (%s)", (_dia(d),))  # type: ignore[arg-type]
        else:
            c.execute(
                f"INSERT INTO {tabla} (tenant_id, creado_en) VALUES (%s, %s)",  # type: ignore[arg-type]
                (tenant, _dia(d)),
            )


def _politica(
    c: psycopg.Connection[dict[str, Any]],
    entidad: str,
    dias: int | None,
    accion: str = "drop",
    columna: str | None = "creado_en",
    tenant: str | None = None,
) -> None:
    c.execute(
        "INSERT INTO core.retention_policies (tenant_id, entity_name, retention_days, action, columna_tiempo, motivo) "
        "VALUES (%s, %s, %s, %s, %s, 'prueba KC-DB-10-4')",
        (tenant, entidad, dias, accion, columna),
    )


# --- cobertura ---------------------------------------------------------------------------------------------------
def test_cubre_todas_las_tablas_declaradas_en_retention_policies(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    declaradas = {
        f["entity_name"]
        for f in conn.execute("SELECT DISTINCT entity_name FROM core.retention_policies").fetchall()
    }
    dedicadas = {
        f["tabla"] for f in conn.execute("SELECT tabla FROM core.retencion_dedicada").fetchall()
    }
    informe = {f["tabla"] for f in _informe(conn) if f["tenant"] is None}
    assert declaradas, "core.retention_policies no puede estar vacía"
    assert declaradas | dedicadas <= informe, (declaradas | dedicadas) - informe


def test_toda_tabla_del_esquema_aparece_una_vez_con_estado_y_justificacion(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    universo = {next(iter(f.values())) for f in conn.execute(UNIVERSO).fetchall()}
    plataforma = [f for f in _informe(conn) if f["tenant"] is None]
    nombres = [f["tabla"] for f in plataforma]
    assert len(nombres) == len(set(nombres)), "una tabla aparece dos veces"
    assert universo <= set(nombres), universo - set(nombres)
    for f in plataforma:
        assert f["estado"] in ESTADOS, f
        assert f["justificacion"] and f["justificacion"].strip(), f
        assert f["desviacion"] == (
            f["estado"] in {"excede", "sin_politica", "no_medible", "inexistente"}
        ), f


def test_en_el_esquema_vigente_no_hay_tablas_sin_politica(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    sin = [f["tabla"] for f in _informe(conn) if f["estado"] in ("sin_politica", "inexistente")]
    assert sin == []
    assert _v26(conn) == 0


def test_las_politicas_comprometidas_figuran_en_el_informe(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    """Webhooks y notificaciones 90 días; auditoría sin borrado (conservar); TTL de idempotencia 48 h."""
    for tabla in ("core.webhook_inbox", "core.notifications"):
        f = _fila(conn, tabla)
        assert (f["origen"], f["accion"], f["retencion"]) == (
            "retention_policies",
            "drop",
            dt.timedelta(days=90),
        )
    audit = _fila(conn, "audit.audit_events")
    assert (audit["accion"], audit["estado"], audit["particionada"]) == (
        "conservar",
        "conservar",
        True,
    )
    idem = _fila(conn, "core.idempotency_keys")
    assert (idem["origen"], idem["retencion"]) == ("purga_dedicada", dt.timedelta(hours=48))


# --- V26 ampliado ------------------------------------------------------------------------------------------------
def test_una_tabla_nueva_sin_politica_es_desviacion_y_v26_la_cuenta(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    antes = _v26(conn)
    t = _tabla(conn, "nueva")  # no particionada: antes de KC-DB-10-4, V26 no la veía
    assert _v26(conn) == antes + 1
    f = _fila(conn, t)
    assert (f["estado"], f["origen"], f["desviacion"], f["alerta"]) == (
        "sin_politica",
        "sin_politica",
        True,
        False,
    )
    assert "sin política" in f["justificacion"]

    _politica(conn, t, None, accion="conservar", columna=None)
    assert _v26(conn) == antes
    assert _fila(conn, t)["estado"] == "conservar"

    conn.execute(
        "UPDATE core.retention_policies SET is_active = false WHERE entity_name = %s", (t,)
    )
    assert _v26(conn) == antes + 1
    assert "desactivada" in _fila(conn, t)["justificacion"]


def test_la_descripcion_de_v26_ya_no_nombra_las_tablas_eliminadas(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    fila = conn.execute(
        "SELECT descripcion FROM core.validar_integridad() WHERE chequeo = 'V26_tabla_sin_retencion'"
    ).fetchone()
    assert fila is not None
    assert "retencion_declaraciones" not in fila["descripcion"]
    assert "core.retention_policies" in fila["descripcion"]


def test_una_politica_de_una_tabla_que_no_existe_se_senala(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    t = f"core.{_nombre('fantasma')}"
    _politica(conn, t, 30)
    f = _fila(conn, t)
    assert (f["estado"], f["desviacion"]) == ("inexistente", True)


# --- desviación deliberada y alerta ------------------------------------------------------------------------------
def test_una_desviacion_deliberada_se_senala_y_dispara_la_alerta(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    t = _tabla(conn, "desv")
    _politica(conn, t, 30)
    _filas(conn, t, 45, 10)  # el dato más antiguo supera los 30 días en 15 (> 7 de tolerancia)
    f = _fila(conn, t)
    assert (f["estado"], f["desviacion"], f["alerta"]) == ("excede", True, True)
    assert f["dato_mas_antiguo"] == _dia(45)
    assert f["exceso"] == dt.timedelta(days=15)
    alertas = conn.execute(
        "SELECT tabla, exceso FROM core.alertas_retencion(%s) WHERE tabla = %s", (AHORA, t)
    ).fetchall()
    assert alertas == [{"tabla": t, "exceso": dt.timedelta(days=15)}]


def test_la_tolerancia_es_de_siete_dias_y_configurable(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    t = _tabla(conn, "tol")
    _politica(conn, t, 30)
    _filas(conn, t, 20)
    assert _fila(conn, t)["estado"] == "cumple"
    _filas(conn, t, 36)  # 6 días fuera de plazo: dentro de la tolerancia
    f = _fila(conn, t)
    assert (f["estado"], f["desviacion"], f["alerta"]) == ("en_tolerancia", False, False)
    assert _fila(conn, t, tolerancia="1 day")["estado"] == "excede"
    assert conn.execute(
        "SELECT count(*) AS n FROM core.alertas_retencion(%s) WHERE tabla = %s", (AHORA, t)
    ).fetchone() == {"n": 0}


def test_una_particionada_se_mide_por_el_limite_superior_de_su_particion(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    """El motor descarta particiones enteras: una fila de 99 días en una partición que vence a los 80 cumple 90."""
    t = f"core.{_nombre('part')}"
    conn.execute(
        f"CREATE TABLE {t} (id serial, creado_en timestamptz NOT NULL) PARTITION BY RANGE (creado_en)"
    )  # type: ignore[arg-type]
    for sufijo, desde, hasta in (("a", 100, 80), ("b", 80, -10)):
        conn.execute(
            f"CREATE TABLE {t}_{sufijo} PARTITION OF {t} FOR VALUES FROM ('{_dia(desde).isoformat()}') "  # type: ignore[arg-type]
            f"TO ('{_dia(hasta).isoformat()}')"
        )
    _politica(conn, t, 90, columna=None)
    _filas(conn, t, 99, 5)
    f = _fila(conn, t)
    assert (f["particionada"], f["particiones"], f["columna_tiempo"]) == (True, 2, "creado_en")
    assert (f["dato_mas_antiguo"], f["referencia"]) == (_dia(99), _dia(80))
    assert (f["estado"], f["alerta"]) == ("cumple", False)

    conn.execute(
        f"CREATE TABLE {t}_z PARTITION OF {t} FOR VALUES FROM ('{_dia(130).isoformat()}') "  # type: ignore[arg-type]
        f"TO ('{_dia(100).isoformat()}')"
    )
    _filas(conn, t, 120)  # su partición venció hace 100 días: 10 fuera de plazo
    f = _fila(conn, t)
    assert (f["referencia"], f["exceso"], f["estado"], f["alerta"]) == (
        _dia(100),
        dt.timedelta(days=10),
        "excede",
        True,
    )


def test_la_purga_corrige_la_desviacion_y_queda_como_ultima_purga(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    # el borrado por lotes del motor (core.borrar_lote_retencion) filtra siempre por tenant_id
    t = _tabla(conn, "purga", con_tenant=True)
    _politica(conn, t, 30)
    _filas(conn, t, 60, 50, 40, 5)
    antes = _fila(conn, t)
    assert (antes["estado"], antes["ultima_purga"]) == ("excede", None)
    conn.execute("SELECT * FROM core.purgar_retencion(%s)", (AHORA,)).fetchall()
    f = _fila(conn, t)
    assert (f["estado"], f["alerta"], f["filas_ultima_purga"]) == ("cumple", False, 3)
    assert f["ultima_purga"] is not None
    assert f["dato_mas_antiguo"] == _dia(5)


def test_una_politica_por_tenant_se_mide_con_su_plazo(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    org = _nombre("org")
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (org, org))
    t = _tabla(conn, "tenant", con_tenant=True)
    _politica(conn, t, 90)
    _politica(conn, t, 30, tenant=org)
    _filas(conn, t, 45, tenant=org)  # vale para la plataforma (90), no para el tenant (30)
    _filas(conn, t, 60, tenant="otro_tenant")
    assert _fila(conn, t)["estado"] == "cumple"
    assert _fila(conn, t)["politicas_tenant"] == 1
    f = _fila(conn, t, tenant=org)
    assert (f["retencion"], f["dato_mas_antiguo"], f["estado"], f["alerta"]) == (
        dt.timedelta(days=30),
        _dia(45),
        "excede",
        True,
    )


def test_una_politica_por_tenant_que_alarga_el_plazo_se_ignora(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    org = _nombre("org")
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (org, org))
    t = _tabla(conn, "largo", con_tenant=True)
    _politica(conn, t, 30)
    _politica(conn, t, 300, tenant=org)
    f = _fila(conn, t, tenant=org)
    assert (f["estado"], f["alerta"]) == ("ignorada", False)


# --- purgas dedicadas y WORM -------------------------------------------------------------------------------------
def test_la_purga_dedicada_se_mide_con_su_filtro(conn: psycopg.Connection[dict[str, Any]]) -> None:
    t = f"core.{_nombre('ded')}"
    conn.execute(
        f"CREATE TABLE {t} (id serial PRIMARY KEY, ref text, creado_en timestamptz NOT NULL)"
    )  # type: ignore[arg-type]
    conn.execute(
        "INSERT INTO core.retencion_dedicada (tabla, plazo, columna_tiempo, columna_nula, ejecutor, motivo) "
        "VALUES (%s, '2 days', 'creado_en', 'ref', 'prueba', 'huérfanos de prueba')",
        (t,),
    )
    conn.execute(f"INSERT INTO {t} (ref, creado_en) VALUES ('asociado', %s)", (_dia(60),))  # type: ignore[arg-type]
    f = _fila(conn, t)
    assert (f["origen"], f["accion"], f["estado"], f["dato_mas_antiguo"]) == (
        "purga_dedicada",
        "purga_dedicada",
        "cumple",
        None,
    )
    assert "Ejecutor: prueba" in f["justificacion"]
    conn.execute(f"INSERT INTO {t} (ref, creado_en) VALUES (NULL, %s)", (_dia(60),))  # type: ignore[arg-type]
    assert _fila(conn, t)["estado"] == "excede"


def test_la_ultima_purga_de_idempotencia_sale_de_purgas_log(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    org = _nombre("org")
    conn.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, %s)", (org, org))
    conn.execute("DELETE FROM core.idempotency_keys")  # se revierte al final
    conn.execute(
        "INSERT INTO core.idempotency_keys (clave, tenant_id, endpoint, respuesta, status_code, creado_en) "
        "VALUES ('k_vieja', %s, 'ep.informe', '{}', 200, %s)",
        (org, _dia(30)),
    )
    assert _fila(conn, "core.idempotency_keys")["estado"] == "excede"
    conn.execute("SELECT core.purgar_idempotency_keys(%s)", (AHORA,))
    f = _fila(conn, "core.idempotency_keys")
    assert (f["estado"], f["filas_ultima_purga"]) == ("cumple", 1)
    assert f["ultima_purga"] is not None


def test_la_auditoria_muestra_sus_particiones_exportadas_a_worm(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    f = _fila(conn, "audit.audit_events")
    vista = conn.execute(
        "SELECT count(*) AS total, count(ultimo_export_id) AS exportadas FROM audit.v_particiones_worm"
    ).fetchone()
    assert vista is not None
    assert f["particiones"] >= 1
    assert f["particiones_worm"] == vista["exportadas"]
    ultimo = conn.execute("SELECT max(verificado_en) AS m FROM audit.worm_exports").fetchone()
    assert ultimo is not None
    assert f["ultimo_export_worm"] == ultimo["m"]


# --- ciclo completo ----------------------------------------------------------------------------------------------
def test_tras_el_ciclo_completo_validar_integridad_no_tiene_hallazgos_nuevos(
    conn: psycopg.Connection[dict[str, Any]],
) -> None:
    """Purga del motor, TTL de idempotencia y minimización de PII; V26 en 0, ningún chequeo empeora y no queda
    ninguna alerta en lo que el ciclo SQL gobierna. (En una base limpia, como la del CI, son 0 hallazgos: lo comprueba
    el paso «Informe de cumplimiento de retención» del job database.)"""
    antes = {
        f["chequeo"]: int(f["problemas"])
        for f in conn.execute("SELECT chequeo, problemas FROM core.validar_integridad()").fetchall()
    }
    conn.execute("SELECT * FROM core.purgar_retencion()").fetchall()
    conn.execute("SELECT core.purgar_idempotency_keys()")
    conn.execute("SELECT * FROM core.minimizar_pii()").fetchall()
    despues = {
        f["chequeo"]: int(f["problemas"])
        for f in conn.execute("SELECT chequeo, problemas FROM core.validar_integridad()").fetchall()
    }
    assert len(despues) == 26
    assert despues["V26_tabla_sin_retencion"] == 0
    assert {k: v for k, v in despues.items() if v > antes.get(k, 0)} == {}
    if sum(antes.values()) == 0:
        assert sum(despues.values()) == 0
    gobernadas = {
        f["tabla"]
        for f in _informe(conn, ahora=dt.datetime.now(dt.UTC))
        if f["alerta"]
        and (f["origen"] == "retention_policies" or f["tabla"] == "core.idempotency_keys")
    }
    assert gobernadas == set()


# --- contrato de la función --------------------------------------------------------------------------------------
def test_una_tolerancia_negativa_se_rechaza(conn: psycopg.Connection[dict[str, Any]]) -> None:
    with pytest.raises(errors.InvalidParameterValue):
        _informe(conn, tolerancia="-1 day")


def test_solo_kallicode_admin_ejecuta_el_informe(conn: psycopg.Connection[dict[str, Any]]) -> None:
    conn.execute("SET LOCAL ROLE kallicode_admin")
    assert _informe(conn)
    conn.execute("RESET ROLE")
    for rol in ("kallicode_app", "kallicode_worker"):
        conn.execute("SAVEPOINT s")
        conn.execute(f"SET LOCAL ROLE {rol}")  # type: ignore[arg-type]
        with pytest.raises(errors.InsufficientPrivilege):
            _informe(conn)
        conn.execute("ROLLBACK TO SAVEPOINT s")
    # V26 sigue al alcance de la aplicación (validar_integridad la ejecuta kallicode_app)
    conn.execute("SET LOCAL ROLE kallicode_app")
    assert len(conn.execute("SELECT * FROM core.validar_integridad()").fetchall()) == 26


def test_el_script_del_informe_usa_la_funcion_y_no_modifica_nada() -> None:
    sql = SCRIPT.read_text(encoding="utf-8")
    codigo = "\n".join(linea for linea in sql.splitlines() if not linea.lstrip().startswith("--"))
    assert "core.reporte_cumplimiento_retencion(" in codigo
    assert not re.search(r"\b(DELETE|UPDATE|INSERT|TRUNCATE|ALTER)\b", codigo, re.IGNORECASE)
    assert not re.search(r"\bpurgar_|\bminimizar_pii\b", codigo)
    # la única tabla que crea y elimina es la temporal del propio informe
    assert re.findall(r"\b(?:CREATE|DROP)\s+(?:TEMP\s+)?TABLE\s+(\w+)", codigo, re.IGNORECASE) == [
        "informe",
        "informe",
    ]
