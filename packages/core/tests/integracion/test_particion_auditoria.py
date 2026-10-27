"""audit.audit_events particionada por rango mensual con pg_partman (KC-DB-07-1, cierra DB-F09).

Comprueba, sobre la base real migrada con 033_particion_auditoria.sql, que la tabla es
particionada y está gestionada por pg_partman, que el trigger de sellado, la inmutabilidad, la
RLS y los permisos siguen intactos, que el mantenimiento crea la partición siguiente sin
intervención, que una consulta por rango de fechas toca solo las particiones implicadas y que la
reversión devuelve la tabla a su forma simple sin perder filas.

La suite de auditoría existente (test_cadena_auditoria.py, test_auditoria_procedencia.py,
test_aislamiento.py...) corre sin cambios sobre la tabla particionada: es la prueba de que
verify_chain, V16 y V17 siguen funcionando.

Todo se ejecuta con el superusuario en una transacción que SIEMPRE se revierte.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
MIGRACION = RAIZ / "db" / "migrations" / "033_particion_auditoria.sql"
REVERSION = RAIZ / "db" / "tools" / "revertir_particion_auditoria.sql"
INDICES_PADRE = (
    "audit_tenant_fecha_ix",
    "audit_ticket_ix",
    "audit_actor_ix",
    "audit_evento_ix",
    "audit_events_sello_ix",
    "audit_events_request_id_ix",
)
_LIMITES = re.compile(r"FROM \('([^']+)'\) TO \('([^']+)'\)")
ORG = "org_particion_test"
ORG_HISTORICO = "org_particion_historico"


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


def _particiones(conn: psycopg.Connection) -> list[tuple[str, datetime, datetime]]:
    """Particiones con rango (sin la DEFAULT), ordenadas por límite inferior."""
    filas = conn.execute(
        """
        SELECT c.relname, pg_get_expr(c.relpartbound, c.oid)
          FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
         WHERE i.inhparent = 'audit.audit_events'::regclass
        """
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
    ahora = datetime.now(UTC)
    return ahora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _crear_org(conn: psycopg.Connection, org: str = ORG) -> None:
    conn.execute(
        "INSERT INTO core.organizations (id, nombre) VALUES (%s, 'Org de prueba') ON CONFLICT DO NOTHING",
        (org,),
    )


def _evento_como_app(conn: psycopg.Connection, n: int) -> None:
    """Inserta n eventos con el rol de la aplicación (RLS activo y trigger de sellado)."""
    conn.execute("SET LOCAL ROLE kallicode_app")
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (ORG,))
    for i in range(n):
        conn.execute(
            "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen) "
            "VALUES (%s, 'sistema', 'evento_prueba', %s)",
            (ORG, f"evento {i}"),
        )
    conn.execute("RESET ROLE")


# --- criterio 1: particionada, con doce particiones futuras y una DEFAULT ------------------------
def test_la_tabla_esta_particionada_por_rango_mensual_de_creado_en(
    conn: psycopg.Connection,
) -> None:
    relkind, estrategia, columna = conn.execute(
        """
        SELECT c.relkind, pt.partstrat,
               (SELECT a.attname FROM pg_attribute a
                 WHERE a.attrelid = c.oid AND a.attnum = pt.partattrs[0])
          FROM pg_class c JOIN pg_partitioned_table pt ON pt.partrelid = c.oid
         WHERE c.oid = 'audit.audit_events'::regclass
        """
    ).fetchone()
    assert (relkind, estrategia, columna) == ("p", "r", "creado_en")

    particiones = _particiones(conn)
    assert particiones, "no hay particiones con rango"
    for nombre, desde, hasta in particiones:
        assert desde.day == 1 and desde.hour == 0, f"{nombre} no empieza el día 1 a las 00:00 UTC"
        meses = (hasta.year - desde.year) * 12 + hasta.month - desde.month
        assert meses == 1 and hasta.day == 1, f"{nombre} no abarca exactamente un mes"
    for (_, _, fin), (_, inicio, _) in pairwise(particiones):
        assert fin == inicio, "las particiones no son contiguas"


def test_tiene_doce_particiones_futuras_precreadas_y_una_default(conn: psycopg.Connection) -> None:
    futuras = [p for p in _particiones(conn) if p[1] > _mes_actual()]
    assert len(futuras) >= 12, f"solo {len(futuras)} particiones futuras: {[p[0] for p in futuras]}"
    assert any(p[1] <= _mes_actual() < p[2] for p in _particiones(conn)), (
        "falta la partición del mes actual"
    )
    assert _uno(conn, "SELECT to_regclass('audit.audit_events_default') IS NOT NULL")
    assert (
        _uno(
            conn,
            "SELECT pg_get_expr(relpartbound, oid) FROM pg_class WHERE oid = 'audit.audit_events_default'::regclass",
        )
        == "DEFAULT"
    )


def test_las_particiones_pertenecen_al_mismo_rol_que_el_padre(conn: psycopg.Connection) -> None:
    """Si el worker de pg_partman corriera con otro rol, las particiones nuevas tendrían otro dueño y la
    reversión (y cualquier DDL de kallicode_admin) fallaría: pg_partman_bgw.role debe ser kallicode_admin."""
    ajenas = conn.execute(
        """
        SELECT c.relname, pg_get_userbyid(c.relowner)
          FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
         WHERE i.inhparent = 'audit.audit_events'::regclass
           AND c.relowner <> (SELECT relowner FROM pg_class WHERE oid = 'audit.audit_events'::regclass)
        """
    ).fetchall()
    assert not ajenas, f"particiones con otro dueño que el padre: {ajenas}"


def test_la_particion_default_esta_vacia(conn: psycopg.Connection) -> None:
    assert _uno(conn, "SELECT count(*) FROM ONLY audit.audit_events_default") == 0


# --- pg_partman gestiona la tabla, sin retención: la auditoría no se borra ----------------------
def test_pg_partman_gestiona_la_tabla_sin_retencion(conn: psycopg.Connection) -> None:
    intervalo, premake, retencion, mantenimiento, infinito = conn.execute(
        """
        SELECT partition_interval, premake, retention, automatic_maintenance, infinite_time_partitions
          FROM partman.part_config WHERE parent_table = 'audit.audit_events'
        """
    ).fetchone()
    assert intervalo == "1 mon"
    assert premake == 12
    assert retencion is None, "la auditoría no se borra: no puede haber retención"
    assert mantenimiento == "on"
    assert infinito is True, "sin esto no se crean particiones nuevas si no hay inserciones"


def test_el_mantenimiento_crea_la_particion_siguiente_sin_intervencion(
    conn: psycopg.Connection,
) -> None:
    antes = len(_particiones(conn))
    # Equivale a que pasen dos meses: pg_partman debe mantener `premake` meses por delante.
    conn.execute(
        "UPDATE partman.part_config SET premake = premake + 2 WHERE parent_table = 'audit.audit_events'"
    )
    conn.execute("SELECT partman.run_maintenance('audit.audit_events')")
    despues = _particiones(conn)
    assert len(despues) >= antes + 2
    assert despues[-1][1] > _mes_actual()


# --- criterio 2: los índices de auditoría son por partición --------------------------------------
def test_los_indices_de_auditoria_son_por_particion(conn: psycopg.Connection) -> None:
    n_particiones = _uno(
        conn, "SELECT count(*) FROM pg_inherits WHERE inhparent = 'audit.audit_events'::regclass"
    )
    for indice in INDICES_PADRE:
        relkind = _uno(
            conn, "SELECT relkind FROM pg_class WHERE oid = %s::regclass", f"audit.{indice}"
        )
        assert relkind == "I", f"{indice} no es un índice de tabla particionada"
        hijos = _uno(
            conn,
            "SELECT count(*) FROM pg_inherits WHERE inhparent = %s::regclass",
            f"audit.{indice}",
        )
        assert hijos == n_particiones, f"{indice}: {hijos} índices para {n_particiones} particiones"


# --- criterio 3: trigger de sellado, inmutabilidad, RLS y permisos intactos ----------------------
def test_los_triggers_estan_en_cada_particion(conn: psycopg.Connection) -> None:
    sin_trigger = conn.execute(
        """
        SELECT c.relname, t.tgname
          FROM pg_inherits i
          JOIN pg_class c ON c.oid = i.inhrelid
          CROSS JOIN (VALUES ('trg_audit_seal'), ('trg_audit_immutable')) AS t(tgname)
         WHERE i.inhparent = 'audit.audit_events'::regclass
           AND NOT EXISTS (SELECT 1 FROM pg_trigger g
                            WHERE g.tgrelid = c.oid AND g.tgname = t.tgname AND NOT g.tgisinternal)
        """
    ).fetchall()
    assert not sin_trigger, f"particiones sin trigger: {sin_trigger}"


def test_la_politica_rls_y_los_permisos_siguen_en_el_padre(conn: psycopg.Connection) -> None:
    rls, forzada = conn.execute(
        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = 'audit.audit_events'::regclass"
    ).fetchone()
    assert (rls, forzada) == (True, True)
    assert (
        _uno(
            conn,
            "SELECT pg_get_expr(polqual, polrelid) FROM pg_policy "
            "WHERE polrelid = 'audit.audit_events'::regclass AND polname = 'tenant_isolation'",
        )
        == "(tenant_id = core.current_tenant())"
    )
    assert _uno(
        conn, "SELECT has_table_privilege('kallicode_app', 'audit.audit_events', 'SELECT, INSERT')"
    )
    for privilegio in ("UPDATE", "DELETE", "TRUNCATE"):
        assert not _uno(
            conn,
            "SELECT has_table_privilege('kallicode_app', 'audit.audit_events', %s)",
            privilegio,
        ), f"kallicode_app no debe tener {privilegio}"


def test_la_aplicacion_no_puede_leer_las_particiones_directamente(conn: psycopg.Connection) -> None:
    """Las particiones no llevan la política RLS del padre: solo se llega a ellas por el padre."""
    particion = _particiones(conn)[0][0]
    conn.execute("SET LOCAL ROLE kallicode_app")
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute(f"SELECT count(*) FROM audit.{particion}")


def test_insertar_por_el_padre_sella_y_cae_en_la_particion_del_mes(
    conn: psycopg.Connection,
) -> None:
    _crear_org(conn)
    _evento_como_app(conn, 3)
    particiones = conn.execute(
        "SELECT DISTINCT tableoid::regclass::text FROM audit.audit_events WHERE tenant_id = %s",
        (ORG,),
    ).fetchall()
    esperado = next(
        n for n, desde, hasta in _particiones(conn) if desde <= datetime.now(UTC) < hasta
    )
    assert particiones == [(f"audit.{esperado}",)]

    integra, verificados, roto = conn.execute(
        "SELECT integra, eventos_verificados, primer_evento_roto FROM audit.verify_chain(%s)",
        (ORG,),
    ).fetchone()
    assert (integra, verificados, roto) == (True, 3, None)


def test_la_inmutabilidad_sigue_vigente_en_la_tabla_particionada(conn: psycopg.Connection) -> None:
    _crear_org(conn)
    _evento_como_app(conn, 1)
    with pytest.raises(errors.RaiseException, match="append-only"):
        conn.execute("DELETE FROM audit.audit_events WHERE tenant_id = %s", (ORG,))


# --- criterio 4: la poda de particiones funciona -------------------------------------------------
def test_una_consulta_por_rango_toca_solo_las_particiones_implicadas(
    conn: psycopg.Connection,
) -> None:
    particiones = [p for p in _particiones(conn) if p[1] > _mes_actual()]
    (n1, desde, _), (n2, _, hasta) = particiones[2], particiones[3]
    plan = "\n".join(
        fila[0]
        for fila in conn.execute(
            "EXPLAIN (COSTS OFF) SELECT id FROM audit.audit_events "
            "WHERE tenant_id = %s AND creado_en >= %s AND creado_en < %s",
            (ORG, desde, hasta),
        ).fetchall()
    )
    tocadas = set(re.findall(r"audit_events_p\d{8}|audit_events_default", plan))
    assert tocadas == {n1, n2}, (
        f"debía tocar solo {n1} y {n2}; el plan toca {sorted(tocadas)}\n{plan}"
    )


# --- la migración es idempotente --------------------------------------------------------------
def test_reaplicar_la_migracion_no_cambia_nada(conn: psycopg.Connection) -> None:
    antes = _uno(
        conn, "SELECT count(*) FROM pg_inherits WHERE inhparent = 'audit.audit_events'::regclass"
    )
    conn.execute(MIGRACION.read_text(encoding="utf-8"))
    conn.execute("RESET ROLE")
    assert (
        _uno(conn, "SELECT relkind FROM pg_class WHERE oid = 'audit.audit_events'::regclass") == "p"
    )
    assert (
        _uno(
            conn,
            "SELECT count(*) FROM pg_inherits WHERE inhparent = 'audit.audit_events'::regclass",
        )
        == antes
    )


# --- criterio 5 (reversión): particionada -> simple -> particionada, sin perder filas -------------
def _sin_meta_ni_transaccion(ruta: Path) -> str:
    """El script de reversión es para psql: se retiran las metaórdenes y su propio BEGIN/COMMIT."""
    lineas = [
        linea
        for linea in ruta.read_text(encoding="utf-8").splitlines()
        if not linea.lstrip().startswith("\\") and linea.strip() not in ("BEGIN;", "COMMIT;")
    ]
    return "\n".join(lineas)


def test_la_reversion_conserva_las_filas_y_se_puede_volver_a_particionar(
    conn: psycopg.Connection,
) -> None:
    _crear_org(conn)
    _crear_org(conn, ORG_HISTORICO)
    _evento_como_app(conn, 5)
    # Un evento antiguo (como el de una base migrada con historia): fuera del trigger de sellado.
    conn.execute("ALTER TABLE audit.audit_events DISABLE TRIGGER trg_audit_seal")
    conn.execute(
        "INSERT INTO audit.audit_events (tenant_id, actor_tipo, evento, resumen, sello_previo, sello, creado_en) "
        "VALUES (%s, 'sistema', 'evento_historico', 'x', %s, %s, now() - interval '20 days')",
        (ORG_HISTORICO, "0" * 64, "a" * 64),
    )
    conn.execute("ALTER TABLE audit.audit_events ENABLE TRIGGER trg_audit_seal")
    total = _uno(conn, "SELECT count(*) FROM audit.audit_events")
    huella = _uno(conn, "SELECT sum(hashtext(sello)::numeric) FROM audit.audit_events")
    max_id = _uno(conn, "SELECT max(id) FROM audit.audit_events")

    conn.execute(_sin_meta_ni_transaccion(REVERSION))
    conn.execute("RESET ROLE")
    assert (
        _uno(conn, "SELECT relkind FROM pg_class WHERE oid = 'audit.audit_events'::regclass") == "r"
    )
    assert _uno(conn, "SELECT count(*) FROM audit.audit_events") == total
    assert _uno(conn, "SELECT sum(hashtext(sello)::numeric) FROM audit.audit_events") == huella
    assert (
        _uno(
            conn,
            "SELECT count(*) FROM partman.part_config WHERE parent_table = 'audit.audit_events'",
        )
        == 0
    )
    assert (
        _uno(
            conn,
            "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'audit.audit_events'::regclass "
            "AND NOT tgisinternal",
        )
        == 2
    )

    # La tabla simple sigue sellando y la identidad continúa donde estaba.
    _evento_como_app(conn, 1)
    assert _uno(conn, "SELECT max(id) FROM audit.audit_events") == max_id + 1
    assert _uno(conn, "SELECT integra FROM audit.verify_chain(%s)", ORG) is True

    # Y se vuelve a particionar con la propia migración.
    conn.execute(MIGRACION.read_text(encoding="utf-8"))
    conn.execute("RESET ROLE")
    assert (
        _uno(conn, "SELECT relkind FROM pg_class WHERE oid = 'audit.audit_events'::regclass") == "p"
    )
    assert _uno(conn, "SELECT count(*) FROM audit.audit_events") == total + 1
    assert _uno(conn, "SELECT count(*) FROM ONLY audit.audit_events_default") == 0
    assert _uno(conn, "SELECT integra FROM audit.verify_chain(%s)", ORG) is True
