"""Purgas automáticas parametrizadas en core.retention_policies (KC-DB-10-1).

Criterios del ticket:
  1. Ningún plazo está escrito en el SQL: cambiar los días de core.retention_policies cambia el comportamiento de la
     siguiente ejecución (test_ningun_plazo..., test_cambiar_los_dias...).
  2. Se insertan registros expirados y vigentes, se ejecuta la purga y solo se eliminan los expirados, sin bloquear
     tablas activas (test_particiones_..., test_filas_..., test_la_purga_no_espera_a_las_filas_bloqueadas).
Además: políticas por tenant (solo acortan), gancho previo al descarte (auditoría sin WORM), 'conservar', registro de
lo hecho, permisos y el trabajo de pg_cron. Casi todo se ejecuta dentro de una transacción que se deshace.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integracion

AHORA = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _dsn() -> str:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    return _normalizar_dsn(url)


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    try:
        c = psycopg.connect(_dsn(), autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    c.execute("SELECT '[1]'::vector")
    try:
        yield c
    finally:
        c.rollback()
        c.close()


def _nombre(prefijo: str) -> str:
    return f"zz_{prefijo}_{uuid.uuid4().hex[:8]}"


def _dia(dias: int) -> str:
    return (AHORA - dt.timedelta(days=dias)).isoformat()


def _politica(
    c: psycopg.Connection,
    entidad: str,
    dias: int | None,
    accion: str = "drop",
    tenant: str | None = None,
    columna: str | None = None,
    lote: int = 100,
) -> None:
    c.execute(
        "INSERT INTO core.retention_policies (tenant_id, entity_name, retention_days, action, columna_tiempo, lote) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (tenant, entidad, dias, accion, columna, lote),
    )


def _cambiar_dias(c: psycopg.Connection, entidad: str, dias: int) -> None:
    c.execute(
        "UPDATE core.retention_policies SET retention_days = %s WHERE entity_name = %s AND tenant_id IS NULL",
        (dias, entidad),
    )


def _tabla_particionada(
    c: psycopg.Connection, nombre: str, tramos: dict[str, tuple[int, int]]
) -> str:
    """Tabla particionada por rango de creado_en; tramos = {sufijo: (días hasta el inicio, días hasta el fin)}."""
    c.execute(
        f"CREATE TABLE core.{nombre} (id serial, tenant_id text, creado_en timestamptz NOT NULL) PARTITION BY RANGE (creado_en)"
    )  # type: ignore[arg-type]
    for sufijo, (desde, hasta) in tramos.items():
        c.execute(  # type: ignore[call-overload]
            f"CREATE TABLE core.{nombre}_{sufijo} PARTITION OF core.{nombre} FOR VALUES FROM ('{_dia(desde)}') TO ('{_dia(hasta)}')"
        )
        c.execute(
            f"INSERT INTO core.{nombre} (tenant_id, creado_en) SELECT 't', %s::timestamptz + interval '1 hour' FROM generate_series(1, 3)",
            (_dia(desde),),
        )  # type: ignore[arg-type]
    return f"core.{nombre}"


def _motor(c: psycopg.Connection, entidad: str, **kw: object) -> list[tuple]:
    args = {"p_ahora": AHORA, "p_simular": False, "p_solo": None, "p_max_lotes": 100, **kw}
    return [
        r
        for r in c.execute(
            "SELECT entidad, tenant, objeto, filas, accion, motivo FROM core.purgar_retencion(%s, %s, %s, %s)",
            (args["p_ahora"], args["p_simular"], args["p_solo"], args["p_max_lotes"]),
        ).fetchall()
        if r[0] == entidad
    ]


def _existe(c: psycopg.Connection, tabla: str) -> bool:
    return c.execute("SELECT to_regclass(%s) IS NOT NULL", (tabla,)).fetchone() == (True,)


def _contar(c: psycopg.Connection, tabla: str, donde: str = "true") -> int:
    fila = c.execute(f"SELECT count(*) FROM {tabla} WHERE {donde}").fetchone()  # type: ignore[arg-type]
    assert fila is not None
    return int(fila[0])


def _organizacion(c: psycopg.Connection, tenant: str) -> None:
    c.execute("INSERT INTO core.organizations (id, nombre) VALUES (%s, 'Org retención')", (tenant,))


# --- criterio 1: ningún plazo hardcodeado ----------------------------------------------------------------
@pytest.mark.parametrize(
    "funcion",
    [
        "core.purgar_retencion(timestamptz, boolean, text, integer)",
        "core.borrar_lote_retencion(regclass, text, timestamptz, text, integer)",
        "core.purgar_particiones_vencidas(timestamptz, boolean)",
        "core.puede_descartar_particion(text, regclass)",
    ],
)
def test_ningun_plazo_de_retencion_esta_escrito_en_el_sql(
    conn: psycopg.Connection, funcion: str
) -> None:
    fila = conn.execute("SELECT pg_get_functiondef(%s::regprocedure)", (funcion,)).fetchone()
    assert fila is not None
    cuerpo = str(fila[0])
    assert not re.search(
        r"\d+\s*(days?|d[ií]as?|months?|meses|weeks?|years?|a[ñn]os)\b", cuerpo, re.IGNORECASE
    ), f"{funcion} tiene un plazo escrito en el SQL"
    assert not re.search(r"interval\s+'\d", cuerpo, re.IGNORECASE)


def test_los_plazos_de_las_particionadas_estan_en_retention_policies(
    conn: psycopg.Connection,
) -> None:
    filas = {
        str(r[0]): int(r[1])
        for r in conn.execute(
            "SELECT entity_name, retention_days FROM core.retention_policies WHERE tenant_id IS NULL AND action = 'drop'"
        ).fetchall()
    }
    assert filas == {"core.webhook_inbox": 90, "core.notifications": 90}
    # y ya no existen las tablas de configuración paralelas
    assert not _existe(conn, "core.retencion_particiones")
    assert not _existe(conn, "core.retencion_declaraciones")


def test_cambiar_los_dias_en_la_politica_cambia_la_siguiente_ejecucion(
    conn: psycopg.Connection,
) -> None:
    t = _nombre("dias")
    tabla = _tabla_particionada(
        conn, t, {"viejo": (110, 100), "medio": (60, 40), "actual": (10, -20)}
    )
    _politica(conn, tabla, 30)
    # con 30 días: vencen la vieja (límite hace 100 d) y la media (límite hace 40 d)
    assert {r[2] for r in _motor(conn, tabla, p_simular=True)} == {
        f"core.{t}_viejo",
        f"core.{t}_medio",
    }
    # con 60 días la media (límite hace 40 d) deja de estar vencida: solo la vieja
    _cambiar_dias(conn, tabla, 60)
    assert {r[2] for r in _motor(conn, tabla, p_simular=True)} == {f"core.{t}_viejo"}
    # con 200 días no vence ninguna
    _cambiar_dias(conn, tabla, 200)
    assert _motor(conn, tabla, p_simular=True) == []


# --- criterio 2: expirados vs vigentes ----------------------------------------------------------------------
def test_particiones_solo_se_elimina_lo_expirado_y_queda_registro(conn: psycopg.Connection) -> None:
    t = _nombre("part")
    tabla = _tabla_particionada(
        conn, t, {"viejo": (110, 100), "medio": (60, 40), "actual": (10, -20)}
    )
    _politica(conn, tabla, 90)
    antes = _contar(conn, "core.retention_purgas")
    r = _motor(conn, tabla)
    assert [(x[2], x[3], x[4]) for x in r] == [(f"core.{t}_viejo", 3, "eliminada")]
    assert not _existe(conn, f"core.{t}_viejo")
    assert _existe(conn, f"core.{t}_medio") and _existe(conn, f"core.{t}_actual")
    assert _contar(conn, tabla) == 6, "las filas vigentes quedan intactas"
    registrado = conn.execute(
        "SELECT accion, filas FROM core.retention_purgas WHERE entidad = %s", (tabla,)
    ).fetchall()
    assert registrado == [("eliminada", 3)]
    assert _contar(conn, "core.retention_purgas") == antes + 1


def test_simular_no_toca_nada_ni_registra(conn: psycopg.Connection) -> None:
    t = _nombre("sim")
    tabla = _tabla_particionada(conn, t, {"viejo": (110, 100), "actual": (10, -20)})
    _politica(conn, tabla, 30)
    r = _motor(conn, tabla, p_simular=True)
    assert [(x[2], x[4]) for x in r] == [(f"core.{t}_viejo", "simulada")]
    assert _existe(conn, f"core.{t}_viejo")
    assert _contar(conn, "core.retention_purgas", f"entidad = '{tabla}'") == 0


def test_detach_y_archive_desacoplan_sin_borrar_los_datos(conn: psycopg.Connection) -> None:
    t = _nombre("det")
    tabla = _tabla_particionada(conn, t, {"viejo": (110, 100), "actual": (10, -20)})
    _politica(conn, tabla, 30, accion="detach")
    r = _motor(conn, tabla)
    assert [(x[2], x[4]) for x in r] == [(f"core.{t}_viejo", "desacoplada")]
    assert _existe(conn, f"core.{t}_viejo") and _contar(conn, f"core.{t}_viejo") == 3
    assert _contar(conn, tabla) == 3, "ya no forma parte de la tabla"

    a = _nombre("arc")
    tabla_a = _tabla_particionada(conn, a, {"viejo": (110, 100), "actual": (10, -20)})
    _politica(conn, tabla_a, 30, accion="archive")
    r = _motor(conn, tabla_a)
    assert [(x[2], x[4]) for x in r] == [(f"retencion_archivo.{a}_viejo", "archivada")]
    assert _contar(conn, f"retencion_archivo.{a}_viejo") == 3


def test_filas_se_borran_por_lotes_acotados_y_solo_las_vencidas(conn: psycopg.Connection) -> None:
    t = _nombre("filas")
    conn.execute(
        f"CREATE TABLE core.{t} (id serial PRIMARY KEY, tenant_id text, creado_en timestamptz NOT NULL)"
    )  # type: ignore[arg-type]
    conn.execute(
        f"INSERT INTO core.{t} (tenant_id, creado_en) SELECT 't', %s::timestamptz FROM generate_series(1, 250)",
        (_dia(100),),
    )  # type: ignore[arg-type]
    conn.execute(
        f"INSERT INTO core.{t} (tenant_id, creado_en) SELECT 't', %s::timestamptz FROM generate_series(1, 20)",
        (_dia(5),),
    )  # type: ignore[arg-type]
    tabla = f"core.{t}"
    _politica(conn, tabla, 30, columna="creado_en", lote=100)
    # con un máximo de 1 lote por ejecución solo se borran 100 y se avisa de que quedan
    r = _motor(conn, tabla, p_max_lotes=1)
    assert (r[0][3], r[0][4]) == (100, "borradas") and "quedan filas vencidas" in (r[0][5] or "")
    assert _contar(conn, tabla) == 170
    # la siguiente ejecución termina (100 + 50) y no toca las vigentes
    r = _motor(conn, tabla)
    assert (r[0][3], r[0][4]) == (150, "borradas") and r[0][5] is None
    assert _contar(conn, tabla) == 20
    assert _contar(conn, tabla, f"creado_en < '{_dia(30)}'") == 0


def test_una_tabla_no_particionada_sin_columna_de_tiempo_se_omite(conn: psycopg.Connection) -> None:
    t = _nombre("sincol")
    conn.execute(f"CREATE TABLE core.{t} (id serial PRIMARY KEY, creado_en timestamptz NOT NULL)")  # type: ignore[arg-type]
    _politica(conn, f"core.{t}", 30)
    r = _motor(conn, f"core.{t}")
    assert [(x[4], x[5]) for x in r] == [
        ("omitida", "la política no indica una columna_tiempo válida")
    ]


def test_una_entidad_inexistente_se_omite_sin_fallar(conn: psycopg.Connection) -> None:
    _politica(conn, "core.zz_no_existe", 30)
    assert [(x[4], x[5]) for x in _motor(conn, "core.zz_no_existe")] == [
        ("omitida", "la entidad no existe")
    ]


# --- políticas por tenant ----------------------------------------------------------------------------------
def test_una_politica_por_tenant_solo_acorta_el_plazo_de_la_plataforma(
    conn: psycopg.Connection,
) -> None:
    t = _nombre("ten")
    conn.execute(
        f"CREATE TABLE core.{t} (id serial PRIMARY KEY, tenant_id text, creado_en timestamptz NOT NULL)"
    )  # type: ignore[arg-type]
    for tenant in ("org_a", "org_b"):
        conn.execute(
            f"INSERT INTO core.{t} (tenant_id, creado_en) SELECT %s, %s::timestamptz FROM generate_series(1, 5)",
            (tenant, _dia(40)),
        )  # type: ignore[arg-type]
    tabla = f"core.{t}"
    suf = uuid.uuid4().hex[:6]
    a, b = f"org_ret_a_{suf}", f"org_ret_b_{suf}"
    _organizacion(conn, a)
    _organizacion(conn, b)
    conn.execute(f"UPDATE core.{t} SET tenant_id = %s WHERE tenant_id = 'org_a'", (a,))  # type: ignore[arg-type]
    conn.execute(f"UPDATE core.{t} SET tenant_id = %s WHERE tenant_id = 'org_b'", (b,))  # type: ignore[arg-type]
    _politica(conn, tabla, 90, columna="creado_en")
    _politica(conn, tabla, 30, tenant=a)  # a quiere conservar 30 días: más corto que la plataforma
    r = _motor(conn, tabla)
    assert [(x[1], x[3], x[4]) for x in r] == [(a, 5, "borradas")]
    assert _contar(conn, tabla, f"tenant_id = '{a}'") == 0
    assert _contar(conn, tabla, f"tenant_id = '{b}'") == 5, "las filas del otro tenant no se tocan"
    # una política por tenant MÁS LARGA que la de plataforma se ignora
    _politica(conn, tabla, 400, tenant=b)
    r = _motor(conn, tabla)
    assert [(x[1], x[4]) for x in r] == [(b, "ignorada")]
    assert _contar(conn, tabla, f"tenant_id = '{b}'") == 5


def test_una_politica_por_tenant_sin_politica_de_plataforma_no_toca_nada(
    conn: psycopg.Connection,
) -> None:
    """El nombre de la tabla nunca sale de lo que escribe un tenant: sin política global no se actúa."""
    suf = uuid.uuid4().hex[:6]
    a = f"org_ret_x_{suf}"
    _organizacion(conn, a)
    conn.execute(
        "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion, creado_en) "
        "VALUES (%s, %s, 'bug', 't', 'd', now() - interval '2 years')",
        (f"tk_{suf}", a),
    )
    _politica(conn, "core.tickets", 1, tenant=a)
    r = conn.execute(
        "SELECT * FROM core.purgar_retencion(now(), false) WHERE entidad = 'core.tickets'"
    ).fetchall()
    assert r == []
    assert _contar(conn, "core.tickets", f"id = 'tk_{suf}'") == 1


# --- 'conservar' y el gancho previo al descarte ------------------------------------------------------------
def test_las_entidades_conservar_no_se_purgan_aunque_pase_el_tiempo(
    conn: psycopg.Connection,
) -> None:
    lejano = AHORA + dt.timedelta(days=5000)
    r = conn.execute("SELECT entidad FROM core.purgar_retencion(%s, true)", (lejano,)).fetchall()
    entidades = {x[0] for x in r}
    for conservada in (
        "audit.audit_events",
        "core.usage_tokens",
        "core.job_transitions",
        "vec.doc_chunks",
    ):
        assert conservada not in entidades


def test_el_gancho_impide_descartar_la_auditoria_sin_comprobante_worm(
    conn: psycopg.Connection,
) -> None:
    particion = conn.execute(
        "SELECT c.oid::regclass::text FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
        "WHERE i.inhparent = 'audit.audit_events'::regclass AND c.relname <> 'audit_events_default' LIMIT 1"
    ).fetchone()
    assert particion is not None
    fila = conn.execute(
        "SELECT permitido, motivo FROM core.puede_descartar_particion('audit.audit_events', %s::regclass)",
        (particion[0],),
    ).fetchone()
    assert fila is not None and fila[0] is False and "WORM" in str(fila[1])
    # aunque alguien cambie la política a DROP, el motor omite la partición y la deja
    conn.execute(
        "UPDATE core.retention_policies SET action = 'drop', retention_days = 1 WHERE entity_name = 'audit.audit_events' AND tenant_id IS NULL"
    )
    lejano = AHORA + dt.timedelta(days=5000)
    r = [
        x
        for x in conn.execute(
            "SELECT entidad, objeto, accion, motivo FROM core.purgar_retencion(%s, false)",
            (lejano,),
        ).fetchall()
        if x[0] == "audit.audit_events"
    ]
    assert r and all(x[2] == "omitida" and "WORM" in str(x[3]) for x in r)
    assert _existe(conn, particion[0])


# --- permisos ---------------------------------------------------------------------------------------------
def test_la_aplicacion_no_puede_ejecutar_el_motor_ni_ver_o_escribir_politicas_globales(
    conn: psycopg.Connection,
) -> None:
    conn.execute("SET LOCAL ROLE kallicode_app")
    conn.execute("SELECT set_config('app.tenant_id', 'org_x', true)")
    conn.execute("SAVEPOINT s")
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute("SELECT * FROM core.purgar_retencion()")
    conn.execute("ROLLBACK TO SAVEPOINT s")
    # las filas globales (tenant_id NULL) son invisibles para la aplicación
    assert conn.execute(
        "SELECT count(*) FROM core.retention_policies WHERE tenant_id IS NULL"
    ).fetchone() == (0,)
    with pytest.raises(
        (errors.InsufficientPrivilege, errors.ForeignKeyViolation, errors.CheckViolation)
    ):
        conn.execute(
            "INSERT INTO core.retention_policies (tenant_id, entity_name, retention_days, action) VALUES (NULL, 'core.x', 5, 'drop')"
        )


def test_una_politica_por_tenant_solo_puede_ser_drop(conn: psycopg.Connection) -> None:
    suf = uuid.uuid4().hex[:6]
    a = f"org_ret_c_{suf}"
    _organizacion(conn, a)
    for accion, dias in (("conservar", None), ("detach", 30), ("archive", 30)):
        conn.execute("SAVEPOINT s")
        with pytest.raises(errors.CheckViolation):
            _politica(conn, "core.webhook_inbox", dias, accion=accion, tenant=a)
        conn.execute("ROLLBACK TO SAVEPOINT s")


# --- pg_cron y chequeos -------------------------------------------------------------------------------------
def test_el_trabajo_de_pg_cron_esta_programado(conn: psycopg.Connection) -> None:
    if conn.execute("SELECT to_regnamespace('cron') IS NULL").fetchone() == (True,):
        pytest.skip("pg_cron no instalada en esta base")
    fila = conn.execute(
        "SELECT schedule, command FROM cron.job WHERE jobname = 'retencion_purgas'"
    ).fetchone()
    assert fila is not None
    assert fila[1].strip().rstrip(";").lower() == "select * from core.purgar_retencion()"
    assert len(fila[0].split()) == 5


def test_v26_retorna_cero_con_las_politicas_vigentes(conn: psycopg.Connection) -> None:
    assert conn.execute(
        "SELECT problemas FROM core.validar_integridad() WHERE chequeo = 'V26_tabla_sin_retencion'"
    ).fetchone() == (0,)


def test_p_solo_invalido_falla(conn: psycopg.Connection) -> None:
    with pytest.raises(errors.RaiseException):
        conn.execute("SELECT * FROM core.purgar_retencion(now(), true, 'todo')")


# --- sin bloquear tablas activas: dos conexiones reales --------------------------------------------------------
def test_la_purga_no_espera_a_las_filas_bloqueadas_por_otra_transaccion() -> None:
    """Otra transacción tiene bloqueada una fila vencida: la purga borra las demás sin esperar, y la borra después."""
    t = _nombre("conc")
    tabla = f"core.{t}"
    a = psycopg.connect(_dsn(), autocommit=True)
    b = psycopg.connect(_dsn(), autocommit=False)
    try:
        a.execute(
            f"CREATE TABLE {tabla} (id serial PRIMARY KEY, tenant_id text, creado_en timestamptz NOT NULL)"
        )  # type: ignore[arg-type]
        a.execute(
            f"INSERT INTO {tabla} (tenant_id, creado_en) SELECT 't', %s::timestamptz FROM generate_series(1, 30)",
            (_dia(100),),
        )  # type: ignore[arg-type]
        _politica(a, tabla, 30, columna="creado_en", lote=100)
        b.execute(f"SELECT id FROM {tabla} WHERE id = 1 FOR UPDATE")  # type: ignore[arg-type]
        a.execute("SET statement_timeout = '8s'")  # si esperase el bloqueo, fallaría por tiempo
        r = _motor(a, tabla)
        assert (r[0][3], r[0][4]) == (29, "borradas")
        assert _contar(a, tabla) == 1
        b.rollback()
        r = _motor(a, tabla)
        assert (r[0][3], r[0][4]) == (1, "borradas")
        assert _contar(a, tabla) == 0
    finally:
        b.rollback()
        b.close()
        a.execute("RESET statement_timeout")
        a.execute("DELETE FROM core.retention_policies WHERE entity_name = %s", (tabla,))
        a.execute("DELETE FROM core.retention_purgas WHERE entidad = %s", (tabla,))
        a.execute(f"DROP TABLE IF EXISTS {tabla}")  # type: ignore[arg-type]
        a.close()
