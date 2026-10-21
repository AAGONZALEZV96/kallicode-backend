"""core.usage_tokens y core.job_transitions particionadas por rango mensual (KC-DB-07-2, DB-F09).

Sigue el patrón de test_particion_auditoria.py (KC-DB-07-1). Comprueba, sobre la base migrada con
035_particion_consumo.sql, que las dos tablas son particionadas con particiones pre-creadas y DEFAULT,
que sus claves ajenas compuestas siguen declaradas y validadas, que una consulta de consumo por ciclo y
una de historial de un job podan particiones (EXPLAIN sobre un conjunto de datos de tres meses), que los
parámetros de autovacuum están fijados, que cada fila aterriza en su partición y ninguna en la DEFAULT, y
que la aplicación no tiene acceso a las particiones (no heredan la política RLS del padre).

Todo se ejecuta con el superusuario en una transacción que SIEMPRE se revierte.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
MIGRACION = RAIZ / "db" / "migrations" / "035_particion_consumo.sql"
_LIMITES = re.compile(r"FROM \('([^']+)'\) TO \('([^']+)'\)")
ORG = "org_consumo_test"
TICKET = "tk_consumo_test"
JOB = "jb_consumo_test"

# tabla -> (índices declarados en el padre, FK compuesta, tabla referenciada, autovacuum esperado)
TABLAS = {
    "core.usage_tokens": {
        "indices": (
            "idx_usage_tokens_tenant_ticket_id",
            "usage_tokens_tenant_ix",
            "usage_tokens_tenant_ticket_etapa_ix",
        ),
        "fk": ("usage_tokens_ticket_id_fkey", "core.tickets"),
        "autovacuum": {
            "autovacuum_vacuum_scale_factor": "0.02",
            "autovacuum_vacuum_insert_scale_factor": "0.02",
            "autovacuum_analyze_scale_factor": "0.01",
        },
    },
    "core.job_transitions": {
        "indices": ("idx_job_transitions_tenant_job_id", "job_transitions_tenant_job_ix"),
        "fk": ("job_transitions_job_id_fkey", "core.jobs"),
        "autovacuum": {
            "autovacuum_vacuum_scale_factor": "0.05",
            "autovacuum_vacuum_insert_scale_factor": "0.05",
            "autovacuum_analyze_scale_factor": "0.02",
        },
    },
}
PARAMETRO_TABLA = pytest.mark.parametrize("tabla", list(TABLAS))


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS (superusuario) no definida")
    try:
        c = psycopg.connect(_normalizar_dsn(url), autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    c.execute("SET TIME ZONE 'UTC'")
    try:
        yield c
    finally:
        c.rollback()  # nada de lo que haga una prueba persiste
        c.close()


def _uno(conn: psycopg.Connection, consulta: str, *params: object):
    fila = conn.execute(consulta, params or None).fetchone()
    assert fila is not None
    return fila[0]


def _particiones(conn: psycopg.Connection, tabla: str) -> list[tuple[str, datetime, datetime]]:
    """Particiones con rango (sin la DEFAULT), ordenadas por límite inferior."""
    filas = conn.execute(
        """
        SELECT c.relname, pg_get_expr(c.relpartbound, c.oid)
          FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
         WHERE i.inhparent = %s::regclass
        """,
        (tabla,),
    ).fetchall()
    salida = []
    for nombre, limite in filas:
        m = _LIMITES.search(limite)
        if m:
            salida.append(
                (
                    nombre,
                    datetime.fromisoformat(m.group(1)).astimezone(UTC),
                    datetime.fromisoformat(m.group(2)).astimezone(UTC),
                )
            )
    return sorted(salida, key=lambda p: p[1])


def _mes_actual() -> datetime:
    return datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _sembrar(conn: psycopg.Connection) -> None:
    """Un tenant con un ticket y un job (el job genera su primera transición, con la fecha de hoy)."""
    conn.execute(
        "INSERT INTO core.organizations (id, nombre) VALUES (%s, 'Org consumo') ON CONFLICT DO NOTHING",
        (ORG,),
    )
    conn.execute(
        "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion) VALUES (%s, %s, 'bug', 'T', 'D')",
        (TICKET, ORG),
    )
    conn.execute(
        "INSERT INTO core.jobs (id, tenant_id, ticket_id, prioridad) VALUES (%s, %s, %s, 'P3')",
        (JOB, ORG, TICKET),
    )


def _transicion(conn: psycopg.Connection) -> None:
    """Una transición del job de hoy (insertar el job no la genera: la registra el cambio de estado)."""
    conn.execute(
        "INSERT INTO core.job_transitions (tenant_id, job_id, de_estado, a_estado) VALUES (%s, %s, 'triage', 'design')",
        (ORG, JOB),
    )


def _tres_meses(conn: psycopg.Connection, tabla: str) -> list[datetime]:
    """Un instante en cada uno de tres meses consecutivos que ya tienen partición (los futuros)."""
    futuras = [p for p in _particiones(conn, tabla) if p[1] > _mes_actual()]
    return [p[1] + timedelta(days=9, hours=3) for p in futuras[:3]]


def _insertar_tres_meses(conn: psycopg.Connection) -> dict[str, list[datetime]]:
    """Dos filas por mes en cada tabla, repartidas en tres meses distintos."""
    _sembrar(conn)
    meses = {t: _tres_meses(conn, t) for t in TABLAS}
    for instante in meses["core.usage_tokens"]:
        for n in range(2):
            conn.execute(
                "INSERT INTO core.usage_tokens (tenant_id, ticket_id, etapa, modelo, tier, tokens_in, tokens_out, "
                "duracion_ms, creado_en) VALUES (%s, %s, 'design', 'm', 'pro', %s, 5, 10, %s)",
                (ORG, TICKET, 10 + n, instante),
            )
    for instante in meses["core.job_transitions"]:
        for n in range(2):
            conn.execute(
                "INSERT INTO core.job_transitions (tenant_id, job_id, de_estado, a_estado, creado_en) "
                "VALUES (%s, %s, 'triage', 'design', %s)",
                (ORG, JOB, instante + timedelta(minutes=n)),
            )
    return meses


# --- criterio 1: particionadas, con particiones pre-creadas y DEFAULT ---------------------------------
@PARAMETRO_TABLA
def test_la_tabla_esta_particionada_por_rango_mensual_de_creado_en(
    conn: psycopg.Connection, tabla: str
) -> None:
    relkind, estrategia, columna = conn.execute(
        """
        SELECT c.relkind, pt.partstrat,
               (SELECT a.attname FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum = pt.partattrs[0])
          FROM pg_class c JOIN pg_partitioned_table pt ON pt.partrelid = c.oid
         WHERE c.oid = %s::regclass
        """,
        (tabla,),
    ).fetchone()
    assert (relkind, estrategia, columna) == ("p", "r", "creado_en")

    particiones = _particiones(conn, tabla)
    assert particiones
    for nombre, desde, hasta in particiones:
        assert desde.day == 1 and desde.hour == 0, f"{nombre} no empieza el día 1 a las 00:00 UTC"
        assert (hasta.year - desde.year) * 12 + hasta.month - desde.month == 1, (
            f"{nombre} no abarca un mes"
        )
    for (_, _, fin), (_, inicio, _) in pairwise(particiones):
        assert fin == inicio, "las particiones no son contiguas"


@PARAMETRO_TABLA
def test_tiene_doce_particiones_futuras_precreadas_y_una_default(
    conn: psycopg.Connection, tabla: str
) -> None:
    particiones = _particiones(conn, tabla)
    assert len([p for p in particiones if p[1] > _mes_actual()]) >= 12
    assert any(p[1] <= _mes_actual() < p[2] for p in particiones), (
        "falta la partición del mes actual"
    )
    assert (
        _uno(
            conn,
            "SELECT pg_get_expr(relpartbound, oid) FROM pg_class WHERE oid = %s::regclass",
            f"{tabla}_default",
        )
        == "DEFAULT"
    )
    assert _uno(conn, f"SELECT count(*) FROM ONLY {tabla}_default") == 0, (
        "la partición DEFAULT debe estar vacía"
    )


@PARAMETRO_TABLA
def test_pg_partman_gestiona_la_tabla_sin_retencion(conn: psycopg.Connection, tabla: str) -> None:
    intervalo, premake, retencion, infinito = conn.execute(
        "SELECT partition_interval, premake, retention, infinite_time_partitions "
        "FROM partman.part_config WHERE parent_table = %s",
        (tabla,),
    ).fetchone()
    assert (intervalo, premake, infinito) == ("1 mon", 12, True)
    assert retencion is None, "la retención la fijará core.retention_policies, no pg_partman"


@PARAMETRO_TABLA
def test_el_mantenimiento_crea_la_particion_siguiente_sin_intervencion(
    conn: psycopg.Connection, tabla: str
) -> None:
    antes = len(_particiones(conn, tabla))
    conn.execute(
        "UPDATE partman.part_config SET premake = premake + 2 WHERE parent_table = %s", (tabla,)
    )
    conn.execute("SELECT partman.run_maintenance(%s)", (tabla,))
    assert len(_particiones(conn, tabla)) == antes + 2


# --- criterio 1 (cont.): las claves ajenas compuestas siguen declaradas y validadas -----------------------
@PARAMETRO_TABLA
def test_las_claves_ajenas_compuestas_siguen_declaradas_y_validadas(
    conn: psycopg.Connection, tabla: str
) -> None:
    nombre_fk, referenciada = TABLAS[tabla]["fk"]
    fk = conn.execute(
        """
        SELECT convalidated, confrelid::regclass::text, confdeltype,
               (SELECT array_agg(a.attname ORDER BY k.ord)
                  FROM unnest(c.conkey) WITH ORDINALITY k(n, ord)
                  JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n)
          FROM pg_constraint c WHERE c.conrelid = %s::regclass AND c.conname = %s
        """,
        (tabla, nombre_fk),
    ).fetchone()
    assert fk is not None, f"falta la FK {nombre_fk}"
    validada, destino, al_borrar, columnas = fk
    assert validada is True
    assert destino == referenciada
    assert al_borrar == "c", "debe seguir siendo ON DELETE CASCADE"
    assert columnas[0] == "tenant_id" and len(columnas) == 2, (
        f"no es compuesta con tenant_id: {columnas}"
    )
    # La FK a core.organizations también sigue ahí.
    assert (
        _uno(
            conn,
            "SELECT count(*) FROM pg_constraint WHERE conrelid = %s::regclass AND contype = 'f' "
            "AND confrelid = 'core.organizations'::regclass AND convalidated",
            tabla,
        )
        == 1
    )
    # Y cada partición tiene sus copias validadas.
    sin_validar = conn.execute(
        """
        SELECT c.conrelid::regclass::text, c.conname FROM pg_constraint c
         WHERE c.contype = 'f' AND c.conparentid <> 0 AND NOT c.convalidated
           AND c.conrelid IN (SELECT inhrelid FROM pg_inherits WHERE inhparent = %s::regclass)
        """,
        (tabla,),
    ).fetchall()
    assert not sin_validar, f"FK sin validar en particiones: {sin_validar}"


def test_la_fk_compuesta_rechaza_un_padre_de_otro_tenant(conn: psycopg.Connection) -> None:
    _sembrar(conn)
    conn.execute(
        "INSERT INTO core.organizations (id, nombre) VALUES ('org_consumo_otro', 'Otra') ON CONFLICT DO NOTHING"
    )
    with pytest.raises(errors.ForeignKeyViolation):
        conn.execute(
            "INSERT INTO core.usage_tokens (tenant_id, ticket_id, etapa, modelo, tier, tokens_in, tokens_out, duracion_ms) "
            "VALUES ('org_consumo_otro', %s, 'design', 'm', 'pro', 1, 1, 1)",
            (TICKET,),
        )


# --- los índices, el trigger y los permisos siguen idénticos ---------------------------------------------
@PARAMETRO_TABLA
def test_los_indices_son_por_particion(conn: psycopg.Connection, tabla: str) -> None:
    n = _uno(conn, "SELECT count(*) FROM pg_inherits WHERE inhparent = %s::regclass", tabla)
    for indice in TABLAS[tabla]["indices"]:
        assert (
            _uno(conn, "SELECT relkind FROM pg_class WHERE oid = %s::regclass", f"core.{indice}")
            == "I"
        )
        hijos = _uno(
            conn,
            "SELECT count(*) FROM pg_inherits WHERE inhparent = %s::regclass",
            f"core.{indice}",
        )
        assert hijos == n, f"{indice}: {hijos} índices para {n} particiones"


def test_job_transitions_sigue_siendo_solo_anadir_en_cada_particion(
    conn: psycopg.Connection,
) -> None:
    sin_trigger = conn.execute(
        """
        SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
         WHERE i.inhparent = 'core.job_transitions'::regclass
           AND NOT EXISTS (SELECT 1 FROM pg_trigger g WHERE g.tgrelid = c.oid
                            AND g.tgname = 'trg_probatoria_append_only' AND NOT g.tgisinternal)
        """
    ).fetchall()
    assert not sin_trigger, f"particiones sin trigger de solo-añadir: {sin_trigger}"
    _sembrar(conn)
    _transicion(conn)
    conn.execute("SAVEPOINT antes")
    with pytest.raises(errors.RaiseException, match="append-only"):
        conn.execute("UPDATE core.job_transitions SET motivo = 'x' WHERE job_id = %s", (JOB,))
    conn.execute("ROLLBACK TO SAVEPOINT antes")
    with pytest.raises(errors.RaiseException, match="append-only"):
        conn.execute("DELETE FROM core.job_transitions WHERE job_id = %s", (JOB,))


def test_borrar_el_job_arrastra_sus_transiciones_por_la_cascada(conn: psycopg.Connection) -> None:
    """La cascada de la FK compuesta sigue funcionando sobre la tabla particionada (profundidad > 1)."""
    _sembrar(conn)
    _transicion(conn)
    assert _uno(conn, "SELECT count(*) FROM core.job_transitions WHERE job_id = %s", JOB) == 1
    conn.execute("DELETE FROM core.jobs WHERE id = %s", (JOB,))
    assert _uno(conn, "SELECT count(*) FROM core.job_transitions WHERE job_id = %s", JOB) == 0


def test_los_permisos_del_padre_son_los_de_antes(conn: psycopg.Connection) -> None:
    for privilegio in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        assert _uno(
            conn, "SELECT has_table_privilege('kallicode_app', 'core.usage_tokens', %s)", privilegio
        )
    for privilegio in ("SELECT", "INSERT"):
        assert _uno(
            conn,
            "SELECT has_table_privilege('kallicode_app', 'core.job_transitions', %s)",
            privilegio,
        )
    for privilegio in ("UPDATE", "DELETE", "TRUNCATE"):
        assert not _uno(
            conn,
            "SELECT has_table_privilege('kallicode_app', 'core.job_transitions', %s)",
            privilegio,
        )
    for tabla in TABLAS:
        rls, forzada = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (tabla,),
        ).fetchone()
        assert (rls, forzada) == (True, True)
        assert (
            _uno(
                conn,
                "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy WHERE polrelid = %s::regclass "
                "AND polname = 'tenant_isolation'",
                tabla,
            )
            == "(tenant_id = core.current_tenant())"
        )


@PARAMETRO_TABLA
def test_la_aplicacion_no_tiene_acceso_a_las_particiones(
    conn: psycopg.Connection, tabla: str
) -> None:
    """Una partición no hereda la política RLS del padre: quien pudiera leerla vería TODOS los tenants.
    04_rls.sql da permisos por defecto a kallicode_app sobre toda tabla nueva de core; el event trigger
    de 00_extensiones.sql se los quita a cada partición, incluidas las que crea el mantenimiento."""
    expuestas = conn.execute(
        """
        SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
         WHERE i.inhparent = %s::regclass
           AND (has_table_privilege('kallicode_app', c.oid, 'SELECT, INSERT, UPDATE, DELETE')
                OR has_table_privilege('kallicode_worker', c.oid, 'SELECT, INSERT, UPDATE, DELETE'))
        """,
        (tabla,),
    ).fetchall()
    assert not expuestas, f"particiones accesibles para la aplicación o el worker: {expuestas}"

    conn.execute(
        "UPDATE partman.part_config SET premake = premake + 1 WHERE parent_table = %s", (tabla,)
    )
    conn.execute("SELECT partman.run_maintenance(%s)", (tabla,))
    nueva = _particiones(conn, tabla)[-1][0]
    assert not _uno(
        conn, "SELECT has_table_privilege('kallicode_app', %s::regclass, 'SELECT')", f"core.{nueva}"
    ), f"la partición nueva {nueva} es legible para la aplicación (¿falta el event trigger?)"

    conn.execute("SET LOCAL ROLE kallicode_app")
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute(f"SELECT count(*) FROM core.{_particiones(conn, tabla)[0][0]}")


def test_el_event_trigger_de_particiones_esta_instalado(conn: psycopg.Connection) -> None:
    assert (
        _uno(
            conn,
            "SELECT evtenabled FROM pg_event_trigger WHERE evtname = 'kc_particiones_sin_acceso'",
        )
        != "D"
    )


@PARAMETRO_TABLA
def test_las_particiones_pertenecen_al_mismo_rol_que_el_padre(
    conn: psycopg.Connection, tabla: str
) -> None:
    ajenas = conn.execute(
        """
        SELECT c.relname, pg_get_userbyid(c.relowner)
          FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
         WHERE i.inhparent = %s::regclass
           AND c.relowner <> (SELECT relowner FROM pg_class WHERE oid = %s::regclass)
        """,
        (tabla, tabla),
    ).fetchall()
    assert not ajenas, f"particiones con otro dueño que el padre: {ajenas}"


# --- criterio 3: autovacuum fijado en el DDL ---------------------------------------------------------------
@PARAMETRO_TABLA
def test_los_parametros_de_autovacuum_estan_fijados_en_la_plantilla_y_en_cada_particion(
    conn: psycopg.Connection, tabla: str
) -> None:
    esperado = TABLAS[tabla]["autovacuum"]

    def opciones(oid_o_nombre: str) -> dict[str, str]:
        fila = conn.execute(
            "SELECT reloptions FROM pg_class WHERE oid = %s::regclass", (oid_o_nombre,)
        ).fetchone()
        return dict(o.split("=") for o in (fila[0] or []))

    plantilla = _uno(
        conn, "SELECT template_table FROM partman.part_config WHERE parent_table = %s", tabla
    )
    assert opciones(plantilla) == esperado, "la plantilla de pg_partman no lleva los parámetros"
    for nombre, _, _ in _particiones(conn, tabla):
        assert opciones(f"core.{nombre}") == esperado, (
            f"{nombre} no lleva los parámetros de autovacuum"
        )
    assert opciones(f"{tabla}_default") == esperado

    # Y las particiones que cree el mantenimiento los heredan de la plantilla.
    conn.execute(
        "UPDATE partman.part_config SET premake = premake + 1 WHERE parent_table = %s", (tabla,)
    )
    conn.execute("SELECT partman.run_maintenance(%s)", (tabla,))
    assert opciones(f"core.{_particiones(conn, tabla)[-1][0]}") == esperado


# --- criterio 2: la poda funciona (EXPLAIN con tres meses de datos) -----------------------------------------
def _plan(conn: psycopg.Connection, consulta: str, *params: object) -> str:
    return "\n".join(
        f[0] for f in conn.execute("EXPLAIN (COSTS OFF) " + consulta, params).fetchall()
    )


def _tocadas(plan: str, tabla: str) -> set[str]:
    base = tabla.split(".")[1]
    return set(re.findall(rf"{base}_p\d{{8}}(?![\w])|{base}_default(?![\w])", plan))


def test_el_consumo_por_ciclo_toca_solo_la_particion_del_ciclo(conn: psycopg.Connection) -> None:
    meses = _insertar_tres_meses(conn)
    mitad = meses["core.usage_tokens"][1]
    inicio = mitad.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    fin = (inicio + timedelta(days=32)).replace(day=1)
    conn.execute("ANALYZE core.usage_tokens")
    consulta = (
        "SELECT sum(tokens_in + tokens_out) FROM core.usage_tokens "
        "WHERE tenant_id = %s AND creado_en >= %s AND creado_en < %s"
    )
    esperada = next(n for n, d, h in _particiones(conn, "core.usage_tokens") if d == inicio)
    tocadas = _tocadas(_plan(conn, consulta, ORG, inicio, fin), "core.usage_tokens")
    assert tocadas == {esperada}, f"debía tocar solo {esperada}; el plan toca {sorted(tocadas)}"
    # El resultado es el del ciclo y solo el del ciclo (2 filas: 10+5 y 11+5).
    assert _uno(conn, consulta, ORG, inicio, fin) == 31


def test_el_historial_de_un_job_toca_solo_las_particiones_de_su_ventana(
    conn: psycopg.Connection,
) -> None:
    meses = _insertar_tres_meses(conn)
    primero, segundo, _ = meses["core.job_transitions"]
    desde = primero.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    hasta = (segundo + timedelta(days=32)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    conn.execute("ANALYZE core.job_transitions")
    consulta = (
        "SELECT de_estado, a_estado, creado_en FROM core.job_transitions "
        "WHERE tenant_id = %s AND job_id = %s AND creado_en >= %s AND creado_en < %s ORDER BY creado_en"
    )
    esperadas = {n for n, d, h in _particiones(conn, "core.job_transitions") if desde <= d < hasta}
    tocadas = _tocadas(_plan(conn, consulta, ORG, JOB, desde, hasta), "core.job_transitions")
    assert len(esperadas) == 2
    assert tocadas == esperadas, f"debía tocar {sorted(esperadas)}; el plan toca {sorted(tocadas)}"
    assert len(conn.execute(consulta, (ORG, JOB, desde, hasta)).fetchall()) == 4


# --- criterio 4: cada fila aterriza en su partición y ninguna cae en la DEFAULT -----------------------------
@PARAMETRO_TABLA
def test_cada_fila_de_tres_meses_aterriza_en_su_particion_y_ninguna_en_la_default(
    conn: psycopg.Connection, tabla: str
) -> None:
    meses = _insertar_tres_meses(conn)
    assert len({m.month for m in meses[tabla]}) == 3, (
        "el conjunto de datos debe abarcar tres meses distintos"
    )
    particiones = {n: (d, h) for n, d, h in _particiones(conn, tabla)}
    filas = conn.execute(
        f"SELECT tableoid::regclass::text, creado_en FROM {tabla} WHERE tenant_id = %s",
        (ORG,),
    ).fetchall()
    assert (
        len(
            [
                f
                for f in filas
                if f[1] in meses[tabla] or f[1] - timedelta(minutes=1) in meses[tabla]
            ]
        )
        >= 6
    )
    for particion, creado in filas:
        nombre = particion.split(".")[-1]
        assert nombre != f"{tabla.split('.')[1]}_default", f"una fila ({creado}) cayó en la DEFAULT"
        desde, hasta = particiones[nombre]
        assert desde <= creado < hasta, f"{creado} está en {nombre} ({desde} - {hasta})"
    assert _uno(conn, f"SELECT count(*) FROM ONLY {tabla}_default") == 0


def test_una_fila_fuera_de_las_particiones_cae_en_la_default_y_se_detecta(
    conn: psycopg.Connection,
) -> None:
    """La DEFAULT existe para no perder escrituras si el mantenimiento se retrasa; que no esté vacía es la señal."""
    _sembrar(conn)
    lejano = _particiones(conn, "core.usage_tokens")[-1][2] + timedelta(days=400)
    conn.execute(
        "INSERT INTO core.usage_tokens (tenant_id, ticket_id, etapa, modelo, tier, tokens_in, tokens_out, duracion_ms, creado_en) "
        "VALUES (%s, %s, 'design', 'm', 'pro', 1, 1, 1, %s)",
        (ORG, TICKET, lejano),
    )
    assert _uno(conn, "SELECT count(*) FROM ONLY core.usage_tokens_default") == 1


# --- la migración es idempotente ---------------------------------------------------------------------------
def test_reaplicar_la_migracion_no_cambia_nada(conn: psycopg.Connection) -> None:
    antes = {t: len(_particiones(conn, t)) for t in TABLAS}
    conn.execute(MIGRACION.read_text(encoding="utf-8"))
    conn.execute("RESET ROLE")
    for t in TABLAS:
        assert _uno(conn, "SELECT relkind FROM pg_class WHERE oid = %s::regclass", t) == "p"
        assert len(_particiones(conn, t)) == antes[t]


def test_v21_ignora_las_fk_clonadas_en_las_particiones(conn: psycopg.Connection) -> None:
    """V21 la ejecuta la aplicación: no puede tropezar con particiones a las que no tiene acceso."""
    conn.execute("SET LOCAL ROLE kallicode_app")
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (ORG,))
    assert (
        _uno(
            conn,
            "SELECT problemas FROM core.validar_integridad() WHERE chequeo = 'V21_coherencia_tenant'",
        )
        == 0
    )
