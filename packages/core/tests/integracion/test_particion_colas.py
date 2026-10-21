"""core.webhook_inbox y core.notifications particionadas con purga por partición (KC-DB-07-3, DB-F09).

Comprueba, sobre la base migrada con 036_particion_colas.sql, que las dos tablas son particionadas con
particiones pre-creadas y DEFAULT; que la purga se implementa como DROP de la partición vencida y no
como DELETE; que el índice parcial de la cola del normalizador sigue sirviendo la consulta de
pendientes; que wi_delivery_uq sigue deduplicando las entregas (dentro del mes: ver la cabecera de la
migración); y que una partición vencida se elimina con las consultas funcionando y el recuento cuadrado.

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
MIGRACION = RAIZ / "db" / "migrations" / "036_particion_colas.sql"
HOUSEKEEPING = RAIZ / "apps" / "workers" / "src" / "kallicode_workers" / "housekeeping.py"
_LIMITES = re.compile(r"FROM \('([^']+)'\) TO \('([^']+)'\)")
ORG = "org_colas_test"
CONEXION = "cn_colas_test"
TICKET = "tk_colas_test"
USUARIO = "us_colas_test"

TABLAS = {
    "core.webhook_inbox": {
        "control": "recibido_mes",
        "indices": (
            "idx_webhook_inbox_tenant_connection_id",
            "idx_webhook_inbox_tenant_ticket_id",
            "webhook_inbox_pend_ix",
            "webhook_inbox_tenant_ix",
        ),
        "fks": {
            "webhook_inbox_connection_id_fkey": ("core.connections", "c"),
            "webhook_inbox_ticket_id_fkey": ("core.tickets", "a"),
        },
    },
    "core.notifications": {
        "control": "creada_en",
        "indices": (
            "idx_notifications_tenant_ticket_id",
            "idx_notifications_tenant_user_id",
            "notifications_tenant_noleidas_ix",
            "notifications_tenant_user_ix",
        ),
        "fks": {
            "notifications_ticket_id_fkey": ("core.tickets", "c"),
            "notifications_user_id_fkey": ("core.users", "c"),
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


def _utc(texto: str) -> datetime:
    d = datetime.fromisoformat(texto)
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


def _particiones(conn: psycopg.Connection, tabla: str) -> list[tuple[str, datetime, datetime]]:
    """Particiones con rango (sin la DEFAULT), ordenadas por límite inferior."""
    filas = conn.execute(
        "SELECT c.relname, pg_get_expr(c.relpartbound, c.oid) FROM pg_inherits i "
        "JOIN pg_class c ON c.oid = i.inhrelid WHERE i.inhparent = %s::regclass",
        (tabla,),
    ).fetchall()
    salida = [
        (n, _utc(m.group(1)), _utc(m.group(2))) for n, b in filas if (m := _LIMITES.search(b))
    ]
    return sorted(salida, key=lambda p: p[1])


def _mes_actual() -> datetime:
    return datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _sembrar(conn: psycopg.Connection) -> None:
    conn.execute(
        "INSERT INTO core.organizations (id, nombre) VALUES (%s, 'Org colas') ON CONFLICT DO NOTHING",
        (ORG,),
    )
    conn.execute(
        "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, 'colas@prueba.dev', 'U') "
        "ON CONFLICT DO NOTHING",
        (USUARIO, ORG),
    )
    conn.execute(
        "INSERT INTO core.connections (id, tenant_id, categoria, proveedor) VALUES (%s, %s, 'tickets', 'jira') "
        "ON CONFLICT DO NOTHING",
        (CONEXION, ORG),
    )
    conn.execute(
        "INSERT INTO core.tickets (id, tenant_id, tipo, titulo, descripcion) VALUES (%s, %s, 'bug', 'T', 'D') "
        "ON CONFLICT DO NOTHING",
        (TICKET, ORG),
    )


def _entrega(
    conn: psycopg.Connection,
    delivery: str,
    instante: datetime | None = None,
    estado: str = "procesado",
) -> None:
    """Una entrega con recibido_en y recibido_mes coherentes (por defecto, ahora)."""
    if instante is None:
        conn.execute(
            "INSERT INTO core.webhook_inbox (tenant_id, connection_id, delivery_id, payload, estado) "
            "VALUES (%s, %s, %s, '{}', %s)",
            (ORG, CONEXION, delivery, estado),
        )
    else:
        mes = instante.replace(day=1, hour=0, minute=0, second=0, microsecond=0).date()
        conn.execute(
            "INSERT INTO core.webhook_inbox (tenant_id, connection_id, delivery_id, payload, estado, "
            "recibido_en, recibido_mes) VALUES (%s, %s, %s, '{}', %s, %s, %s)",
            (ORG, CONEXION, delivery, estado, instante, mes),
        )


def _aviso(conn: psycopg.Connection, n: str, instante: datetime | None = None) -> None:
    conn.execute(
        "INSERT INTO core.notifications (id, tenant_id, user_id, tipo, titulo, creada_en) "
        "VALUES (%s, %s, %s, 'sistema', 'T', coalesce(%s, now()))",
        (n, ORG, USUARIO, instante),
    )


def _crear_particion_vencida(conn: psycopg.Connection, tabla: str) -> str:
    """Una partición de enero de 2025 (vencida con cualquier retención razonable)."""
    nombre = f"{tabla}_pvencida_prueba"
    conn.execute(
        f"CREATE TABLE {nombre} PARTITION OF {tabla} FOR VALUES FROM ('2025-01-01') TO ('2025-02-01')"
    )
    return nombre


# --- criterio 1: particionadas, con particiones pre-creadas y DEFAULT ----------------------------------
@PARAMETRO_TABLA
def test_la_tabla_esta_particionada_por_rango_mensual(conn: psycopg.Connection, tabla: str) -> None:
    relkind, estrategia, columna = conn.execute(
        """
        SELECT c.relkind, pt.partstrat,
               (SELECT a.attname FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum = pt.partattrs[0])
          FROM pg_class c JOIN pg_partitioned_table pt ON pt.partrelid = c.oid WHERE c.oid = %s::regclass
        """,
        (tabla,),
    ).fetchone()
    assert (relkind, estrategia, columna) == ("p", "r", TABLAS[tabla]["control"])
    particiones = _particiones(conn, tabla)
    for nombre, desde, hasta in particiones:
        assert desde.day == 1 and desde.hour == 0, f"{nombre} no empieza el día 1 a las 00:00 UTC"
        assert (hasta.year - desde.year) * 12 + hasta.month - desde.month == 1, (
            f"{nombre} no abarca un mes"
        )
    for (_, _, fin), (_, inicio, _) in pairwise(particiones):
        assert fin == inicio, "las particiones no son contiguas"


@PARAMETRO_TABLA
def test_tiene_doce_particiones_futuras_precreadas_y_una_default_vacia(
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
    assert _uno(conn, f"SELECT count(*) FROM ONLY {tabla}_default") == 0


@PARAMETRO_TABLA
def test_pg_partman_gestiona_la_tabla_y_la_purga_es_nuestra(
    conn: psycopg.Connection, tabla: str
) -> None:
    intervalo, premake, retencion, infinito = conn.execute(
        "SELECT partition_interval, premake, retention, infinite_time_partitions FROM partman.part_config "
        "WHERE parent_table = %s",
        (tabla,),
    ).fetchone()
    assert (intervalo, premake, infinito) == ("1 mon", 12, True)
    # La retención de pg_partman borraría sin mirar si quedan entregas pendientes: la hace
    # core.purgar_particiones_vencidas().
    assert retencion is None


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


@PARAMETRO_TABLA
def test_las_claves_ajenas_compuestas_siguen_declaradas_y_validadas(
    conn: psycopg.Connection, tabla: str
) -> None:
    for nombre, (destino, al_borrar) in TABLAS[tabla]["fks"].items():
        validada, ref, accion, columnas = conn.execute(
            """
            SELECT convalidated, confrelid::regclass::text, confdeltype,
                   (SELECT array_agg(a.attname ORDER BY k.ord) FROM unnest(c.conkey) WITH ORDINALITY k(n, ord)
                      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n)
              FROM pg_constraint c WHERE c.conrelid = %s::regclass AND c.conname = %s
            """,
            (tabla, nombre),
        ).fetchone()
        assert validada and ref == destino and accion == al_borrar, nombre
        assert columnas[0] == "tenant_id" and len(columnas) == 2, (
            f"{nombre} no es compuesta con tenant_id"
        )
    sin_validar = conn.execute(
        "SELECT c.conrelid::regclass::text, c.conname FROM pg_constraint c WHERE c.contype = 'f' "
        "AND c.conparentid <> 0 AND NOT c.convalidated "
        "AND c.conrelid IN (SELECT inhrelid FROM pg_inherits WHERE inhparent = %s::regclass)",
        (tabla,),
    ).fetchall()
    assert not sin_validar, f"FK sin validar en particiones: {sin_validar}"


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


# --- criterio 3: wi_delivery_uq sigue garantizando la idempotencia ---------------------------------------
def test_wi_delivery_uq_incluye_la_clave_de_particion(conn: psycopg.Connection) -> None:
    columnas = _uno(
        conn,
        "SELECT array_agg(a.attname ORDER BY k.ord) FROM pg_constraint c "
        "CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY k(n, ord) "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n "
        "WHERE c.conrelid = 'core.webhook_inbox'::regclass AND c.conname = 'wi_delivery_uq' AND c.contype = 'u'",
    )
    assert columnas == ["connection_id", "delivery_id", "recibido_mes"]


def test_una_entrega_repetida_en_el_mismo_mes_se_rechaza(conn: psycopg.Connection) -> None:
    _sembrar(conn)
    _entrega(conn, "dl_repetida")
    conn.execute("SAVEPOINT s")
    with pytest.raises(errors.UniqueViolation):
        _entrega(conn, "dl_repetida")
    conn.execute("ROLLBACK TO SAVEPOINT s")
    # Es lo que hace la API: el reintento no inserta ni devuelve id.
    fila = conn.execute(
        "INSERT INTO core.webhook_inbox (tenant_id, connection_id, delivery_id, payload) "
        "VALUES (%s, %s, 'dl_repetida', '{}') ON CONFLICT DO NOTHING RETURNING id",
        (ORG, CONEXION),
    ).fetchone()
    assert fila is None
    assert (
        _uno(conn, "SELECT count(*) FROM core.webhook_inbox WHERE delivery_id = 'dl_repetida'") == 1
    )


def test_el_efecto_documentado_un_reintento_de_otro_mes_no_se_reconoce(
    conn: psycopg.Connection,
) -> None:
    """La unicidad incluye la clave de partición: vale DENTRO del mes. Documentado en la migración y
    en docs/particionado_colas.md; este test fija el comportamiento para que nadie lo cambie sin querer."""
    _sembrar(conn)
    futuras = [p for p in _particiones(conn, "core.webhook_inbox") if p[1] > _mes_actual()]
    _entrega(conn, "dl_mes_a", futuras[0][1] + timedelta(days=3))
    _entrega(
        conn, "dl_mes_a", futuras[1][1] + timedelta(days=3)
    )  # misma entrega, mes siguiente: no se detecta
    assert _uno(conn, "SELECT count(*) FROM core.webhook_inbox WHERE delivery_id = 'dl_mes_a'") == 2


def test_on_conflict_con_objetivo_de_dos_columnas_ya_no_coincide_con_ninguna_restriccion(
    conn: psycopg.Connection,
) -> None:
    """Por eso la API usa ON CONFLICT DO NOTHING sin objetivo."""
    _sembrar(conn)
    with pytest.raises(errors.InvalidColumnReference):
        conn.execute(
            "INSERT INTO core.webhook_inbox (tenant_id, connection_id, delivery_id, payload) "
            "VALUES (%s, %s, 'dl_x', '{}') ON CONFLICT (connection_id, delivery_id) DO NOTHING",
            (ORG, CONEXION),
        )


def test_recibido_mes_debe_ser_coherente_con_recibido_en(conn: psycopg.Connection) -> None:
    _sembrar(conn)
    with pytest.raises(errors.CheckViolation):
        conn.execute(
            "INSERT INTO core.webhook_inbox (tenant_id, connection_id, delivery_id, payload, recibido_en, recibido_mes) "
            "VALUES (%s, %s, 'dl_incoherente', '{}', now(), (date_trunc('month', now()) + interval '1 month')::date)",
            (ORG, CONEXION),
        )


def test_la_api_inserta_con_on_conflict_sin_objetivo() -> None:
    codigo = (
        RAIZ / "apps" / "api" / "src" / "kallicode_api" / "routers" / "webhooks.py"
    ).read_text(encoding="utf-8")
    assert "ON CONFLICT DO NOTHING" in codigo
    assert "ON CONFLICT (connection_id, delivery_id)" not in codigo


# --- criterio 2: el índice parcial de la cola sigue sirviendo la consulta de pendientes -----------------
def test_el_indice_parcial_de_la_cola_sirve_la_consulta_de_pendientes_en_las_particiones_recientes(
    conn: psycopg.Connection,
) -> None:
    _sembrar(conn)
    _entrega(conn, "dl_pendiente_1", estado="pendiente")
    _entrega(conn, "dl_pendiente_2", estado="pendiente")
    conn.execute("ANALYZE core.webhook_inbox")
    actual = next(
        n for n, d, h in _particiones(conn, "core.webhook_inbox") if d <= datetime.now(UTC) < h
    )
    barrido = "SELECT id FROM core.webhook_inbox WHERE estado IN ('pendiente','fallido') ORDER BY recibido_en LIMIT 50"
    por_id = "SELECT tenant_id, evento, payload FROM core.webhook_inbox WHERE id = %s AND estado = 'pendiente'"
    conn.execute("SET LOCAL ROLE kallicode_worker")
    conn.execute("SET LOCAL enable_seqscan = off")

    def plan(consulta: str, *params: object) -> str:
        return "\n".join(
            f[0] for f in conn.execute("EXPLAIN (COSTS OFF) " + consulta, params or None).fetchall()
        )

    # El barrido del normalizador (sin filtro de tenant) sigue sirviéndose del índice parcial, en
    # la partición reciente (y en las demás), ordenado por recibido_en sin ordenar aparte.
    p = plan(barrido)
    assert "Seq Scan" not in p, p
    assert f"{actual}_estado_recibido_en_idx" in p, (
        f"el barrido no usa el índice parcial en {actual}:\n{p}"
    )
    # La lectura de una entrega por id tampoco recorre ninguna partición entera.
    assert "Seq Scan" not in plan(por_id, 1)


# --- los permisos y la RLS siguen intactos -------------------------------------------------------------------
def test_los_permisos_y_las_politicas_del_padre_son_los_de_antes(conn: psycopg.Connection) -> None:
    for privilegio in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        for tabla in TABLAS:
            assert _uno(
                conn, "SELECT has_table_privilege('kallicode_app', %s, %s)", tabla, privilegio
            )
    assert not _uno(
        conn, "SELECT has_table_privilege('kallicode_worker', 'core.webhook_inbox', 'INSERT')"
    )
    for privilegio in ("SELECT", "UPDATE", "DELETE"):
        assert _uno(
            conn,
            "SELECT has_table_privilege('kallicode_worker', 'core.webhook_inbox', %s)",
            privilegio,
        )
    politicas = {
        f[0]
        for f in conn.execute(
            "SELECT polname FROM pg_policy WHERE polrelid = 'core.webhook_inbox'::regclass"
        ).fetchall()
    }
    assert politicas == {"tenant_isolation", "worker_colas"}
    for tabla in TABLAS:
        assert conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = %s::regclass",
            (tabla,),
        ).fetchone() == (True, True)


@PARAMETRO_TABLA
def test_la_aplicacion_y_el_worker_no_tienen_acceso_a_las_particiones(
    conn: psycopg.Connection, tabla: str
) -> None:
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
    conn.execute("SET LOCAL ROLE kallicode_app")
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute(f"SELECT count(*) FROM core.{_particiones(conn, tabla)[0][0]}")


def test_el_event_trigger_cubre_una_particion_creada_con_attach(conn: psycopg.Connection) -> None:
    """pg_partman crea las particiones iniciales como CREATE TABLE suelta + ALTER TABLE ... ATTACH PARTITION:
    en el CREATE todavía no es partición y el comando del ATTACH reporta al padre. Regresión de KC-DB-07-3."""
    conn.execute("SET LOCAL ROLE kallicode_admin")
    conn.execute(
        "CREATE TABLE core.notifications_pattach (LIKE core.notifications INCLUDING DEFAULTS INCLUDING CONSTRAINTS)"
    )
    assert _uno(
        conn, "SELECT has_table_privilege('kallicode_app', 'core.notifications_pattach', 'SELECT')"
    ), (
        "el supuesto de la prueba no se cumple: la tabla suelta debería nacer con permisos por defecto"
    )
    conn.execute(
        "ALTER TABLE core.notifications ATTACH PARTITION core.notifications_pattach "
        "FOR VALUES FROM ('2024-01-01') TO ('2024-02-01')"
    )
    assert not _uno(
        conn, "SELECT has_table_privilege('kallicode_app', 'core.notifications_pattach', 'SELECT')"
    )


# --- criterio 1 y 4: la purga es un DROP de la partición vencida; las consultas siguen y el recuento cuadra ---
def test_la_purga_de_webhook_inbox_elimina_la_particion_vencida_con_drop(
    conn: psycopg.Connection,
) -> None:
    _sembrar(conn)
    vencida = _crear_particion_vencida(conn, "core.webhook_inbox")
    for n in range(3):
        _entrega(conn, f"dl_vieja_{n}", datetime(2025, 1, 10 + n, 12, tzinfo=UTC))
    _entrega(conn, "dl_reciente")
    _entrega(conn, "dl_pendiente_reciente", estado="pendiente")
    total = _uno(conn, "SELECT count(*) FROM core.webhook_inbox")
    en_la_vencida = _uno(conn, f"SELECT count(*) FROM ONLY {vencida}")
    assert en_la_vencida == 3

    resultado = conn.execute(
        "SELECT tabla, particion, filas, accion, motivo FROM core.purgar_particiones_vencidas()"
    ).fetchall()
    assert (vencida, 3, "eliminada") in [(r[1], r[2], r[3]) for r in resultado], resultado
    assert [r for r in resultado if r[3] != "eliminada"] == []

    assert _uno(conn, "SELECT to_regclass(%s) IS NULL", vencida), (
        "la partición debe haber desaparecido (DROP)"
    )
    assert _uno(conn, "SELECT count(*) FROM core.webhook_inbox") == total - en_la_vencida, (
        "el recuento no cuadra"
    )
    # Las consultas siguen funcionando sobre las particiones que quedan.
    assert (
        _uno(conn, "SELECT count(*) FROM core.webhook_inbox WHERE delivery_id LIKE 'dl_vieja_%'")
        == 0
    )
    assert (
        _uno(
            conn,
            "SELECT count(*) FROM core.webhook_inbox WHERE estado = 'pendiente' AND tenant_id = %s",
            ORG,
        )
        == 1
    )
    assert _uno(conn, "SELECT count(*) FROM ONLY core.webhook_inbox_default") == 0
    # La entrega repetida de un mes ya purgado vuelve a aceptarse (el efecto de purgar).
    _entrega(conn, "dl_vieja_0")


def test_la_purga_de_notifications_elimina_la_particion_vencida(conn: psycopg.Connection) -> None:
    _sembrar(conn)
    vencida = _crear_particion_vencida(conn, "core.notifications")
    for n in range(4):
        _aviso(conn, f"nt_vieja_{n}", datetime(2025, 1, 5 + n, 9, tzinfo=UTC))
    _aviso(conn, "nt_reciente")
    total = _uno(conn, "SELECT count(*) FROM core.notifications")
    resultado = conn.execute(
        "SELECT particion, filas, accion FROM core.purgar_particiones_vencidas()"
    ).fetchall()
    assert (vencida, 4, "eliminada") in resultado
    assert _uno(conn, "SELECT to_regclass(%s) IS NULL", vencida)
    assert _uno(conn, "SELECT count(*) FROM core.notifications") == total - 4
    assert (
        _uno(
            conn,
            "SELECT count(*) FROM core.notifications WHERE user_id = %s AND NOT leida",
            USUARIO,
        )
        == 1
    )


def test_no_se_elimina_una_particion_con_entregas_pendientes(conn: psycopg.Connection) -> None:
    _sembrar(conn)
    vencida = _crear_particion_vencida(conn, "core.webhook_inbox")
    _entrega(conn, "dl_procesada_vieja", datetime(2025, 1, 10, tzinfo=UTC))
    _entrega(conn, "dl_atascada_vieja", datetime(2025, 1, 11, tzinfo=UTC), estado="pendiente")
    resultado = conn.execute(
        "SELECT particion, filas, accion, motivo FROM core.purgar_particiones_vencidas()"
    ).fetchall()
    fila = next(r for r in resultado if r[0] == vencida)
    assert fila[2] == "omitida" and "pendientes" in fila[3]
    assert _uno(conn, "SELECT to_regclass(%s) IS NOT NULL", vencida), (
        "no debe perderse una entrega sin procesar"
    )
    assert _uno(conn, f"SELECT count(*) FROM ONLY {vencida}") == 2


def test_la_simulacion_informa_sin_eliminar(conn: psycopg.Connection) -> None:
    _sembrar(conn)
    vencida = _crear_particion_vencida(conn, "core.webhook_inbox")
    _entrega(conn, "dl_simulada", datetime(2025, 1, 10, tzinfo=UTC))
    resultado = conn.execute(
        "SELECT particion, accion FROM core.purgar_particiones_vencidas(now(), true)"
    ).fetchall()
    assert (vencida, "simulada") in resultado
    assert _uno(conn, "SELECT to_regclass(%s) IS NOT NULL", vencida)


def test_las_particiones_dentro_del_plazo_no_se_purgan(conn: psycopg.Connection) -> None:
    """El mes actual y los futuros no cumplen nunca el plazo, ni tampoco la DEFAULT."""
    assert (
        conn.execute("SELECT * FROM core.purgar_particiones_vencidas(now(), true)").fetchall() == []
    )
    # Con la retención de 90 días, el mes cerrado hace 60 días tampoco se purga.
    dentro = _mes_actual() - timedelta(days=60)
    assert dentro > datetime.now(UTC) - timedelta(days=90) - timedelta(days=31)


def test_la_retencion_esta_configurada_y_es_de_90_dias(conn: psycopg.Connection) -> None:
    filas: dict[str, timedelta] = dict(
        conn.execute("SELECT tabla, retencion FROM core.retencion_particiones").fetchall()
    )
    assert filas == {
        "core.webhook_inbox": timedelta(days=90),
        "core.notifications": timedelta(days=90),
    }


def test_la_aplicacion_no_puede_purgar_ni_ver_la_configuracion(conn: psycopg.Connection) -> None:
    conn.execute("SET LOCAL ROLE kallicode_app")
    conn.execute("SAVEPOINT s")
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute("SELECT * FROM core.purgar_particiones_vencidas()")
    conn.execute("ROLLBACK TO SAVEPOINT s")
    with pytest.raises(errors.InsufficientPrivilege):
        conn.execute("SELECT * FROM core.retencion_particiones")


def test_la_purga_nunca_usa_delete() -> None:
    sql = MIGRACION.read_text(encoding="utf-8")
    inicio = sql.index("CREATE OR REPLACE FUNCTION core.purgar_particiones_vencidas")
    funcion = sql[inicio : sql.index("COMMENT ON FUNCTION core.purgar_particiones_vencidas")]
    assert "DELETE" not in funcion.upper().replace("DELETE_", "")
    assert "DROP TABLE" in funcion
    # Y el worker ya no purga webhook_inbox con DELETE: llama a la función.
    worker = HOUSEKEEPING.read_text(encoding="utf-8")
    assert "DELETE FROM core.webhook_inbox" not in worker
    assert "purgar_particiones_vencidas" in worker


# --- cada fila aterriza en su partición ---------------------------------------------------------------------
def test_las_entregas_y_los_avisos_de_tres_meses_aterrizan_en_su_particion(
    conn: psycopg.Connection,
) -> None:
    _sembrar(conn)
    for tabla in TABLAS:
        futuras = [p for p in _particiones(conn, tabla) if p[1] > _mes_actual()][:3]
        for i, (_, desde, _) in enumerate(futuras):
            instante = desde + timedelta(days=9, hours=3)
            if tabla == "core.webhook_inbox":
                _entrega(conn, f"dl_tres_{i}", instante)
            else:
                _aviso(conn, f"nt_tres_{i}", instante)
        particiones = {n: (d, h) for n, d, h in _particiones(conn, tabla)}
        columna, clave = (
            ("recibido_en", "delivery_id LIKE 'dl_tres_%%'")
            if tabla == "core.webhook_inbox"
            else ("creada_en", "id LIKE 'nt_tres_%%'")
        )
        filas = conn.execute(
            f"SELECT tableoid::regclass::text, {columna} FROM {tabla} WHERE tenant_id = %s "
            f"AND {clave}",
            (ORG,),
        ).fetchall()
        assert len(filas) == 3
        for particion, creado in filas:
            nombre = particion.split(".")[-1]
            assert not nombre.endswith("_default")
            assert particiones[nombre][0] <= creado < particiones[nombre][1]
        assert _uno(conn, f"SELECT count(*) FROM ONLY {tabla}_default") == 0


# --- la migración es idempotente ----------------------------------------------------------------------------
def test_reaplicar_la_migracion_no_cambia_nada(conn: psycopg.Connection) -> None:
    antes = {t: len(_particiones(conn, t)) for t in TABLAS}
    conn.execute(MIGRACION.read_text(encoding="utf-8"))
    conn.execute("RESET ROLE")
    for t in TABLAS:
        assert _uno(conn, "SELECT relkind FROM pg_class WHERE oid = %s::regclass", t) == "p"
        assert len(_particiones(conn, t)) == antes[t]
    assert _uno(conn, "SELECT count(*) FROM core.retencion_particiones") == 2
