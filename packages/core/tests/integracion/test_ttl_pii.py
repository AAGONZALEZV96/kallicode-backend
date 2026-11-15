"""TTL de core.idempotency_keys y minimización de PII (KC-DB-10-3).

Criterios de aceptación:
- Las claves de idempotencia expiradas se purgan sin afectar a las que siguen dentro de su ventana TTL.
- Los campos PII marcados quedan anonimizados al cumplirse su ventana operativa.

El paso del tiempo se simula con el parámetro p_ahora de las funciones, de modo que la prueba es
determinista y no depende del reloj. Todo corre con el superusuario en una transacción que SIEMPRE se
revierte. Requiere la migración 055 y KC_DATABASE_URL_MIGRATIONS.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.integracion

RAIZ = Path(__file__).resolve().parents[4]
MIGRACION = RAIZ / "db" / "migrations" / "055_ttl_idempotencia_minimizacion_pii.sql"
HOUSEKEEPING = RAIZ / "apps" / "workers" / "src" / "kallicode_workers" / "housekeeping.py"

ORG_A = "org_ttl_a"
ORG_B = "org_ttl_b"
USUARIO = {ORG_A: "us_ttl_a", ORG_B: "us_ttl_b"}
AHORA = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


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
    try:
        existe = c.execute("SELECT to_regproc('core.purgar_idempotency_keys')").fetchone()
        if not existe or existe[0] is None:
            pytest.skip("migración 055 sin aplicar")
        c.execute("SET TIME ZONE 'UTC'")
        for org in (ORG_A, ORG_B):
            c.execute(
                "INSERT INTO core.organizations (id, nombre) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (org, org),
            )
            c.execute(
                "INSERT INTO core.users (id, tenant_id, email, nombre) VALUES (%s, %s, %s, 'U') "
                "ON CONFLICT DO NOTHING",
                (USUARIO[org], org, f"{USUARIO[org]}@prueba.dev"),
            )
        yield c
    finally:
        c.rollback()  # nada de lo que haga una prueba persiste
        c.close()


def _clave(conn: psycopg.Connection, tenant: str, clave: str, creada: datetime, status: int = 200):
    conn.execute(
        "INSERT INTO core.idempotency_keys (clave, tenant_id, endpoint, respuesta, status_code, creado_en)"
        " VALUES (%s, %s, 'ep.test', '{}', %s, %s)",
        (clave, tenant, status, creada),
    )


def _claves(conn: psycopg.Connection) -> set[str]:
    filas = conn.execute(
        "SELECT clave FROM core.idempotency_keys WHERE endpoint = 'ep.test'"
    ).fetchall()
    return {f[0] for f in filas}


def _purgar(conn: psycopg.Connection, **kw: object) -> int:
    if "ttl" in kw:
        fila = conn.execute(
            "SELECT core.purgar_idempotency_keys(%s, %s)", (AHORA, kw["ttl"])
        ).fetchone()
    else:
        fila = conn.execute("SELECT core.purgar_idempotency_keys(%s)", (AHORA,)).fetchone()
    assert fila is not None
    return int(fila[0])


# ---------------------------------------------------------------------------
# TTL de idempotencia
# ---------------------------------------------------------------------------
def test_purga_las_vencidas_y_conserva_las_activas_en_todos_los_tenants(
    conn: psycopg.Connection,
) -> None:
    _clave(conn, ORG_A, "vencida_a", AHORA - timedelta(hours=49))
    _clave(conn, ORG_B, "vencida_b", AHORA - timedelta(days=30))
    _clave(conn, ORG_A, "activa_47h", AHORA - timedelta(hours=47))
    _clave(conn, ORG_B, "activa_ahora", AHORA)
    _clave(conn, ORG_A, "reserva_en_curso", AHORA - timedelta(minutes=5), status=0)
    assert _purgar(conn) == 2
    assert _claves(conn) == {"activa_47h", "activa_ahora", "reserva_en_curso"}


def test_el_limite_del_ttl_es_exacto(conn: psycopg.Connection) -> None:
    _clave(conn, ORG_A, "justo_en_el_limite", AHORA - timedelta(hours=48))
    _clave(conn, ORG_A, "un_segundo_pasado", AHORA - timedelta(hours=48, seconds=1))
    assert _purgar(conn) == 1
    assert _claves(conn) == {"justo_en_el_limite"}


def test_una_reserva_con_latido_no_se_purga_aunque_sea_antigua(conn: psycopg.Connection) -> None:
    """El worker de ingesta renueva creado_en con su latido: una reserva viva siempre está reciente."""
    _clave(conn, ORG_A, "larga", AHORA - timedelta(days=3), status=0)
    conn.execute("UPDATE core.idempotency_keys SET creado_en = %s WHERE clave = 'larga'", (AHORA,))
    assert _purgar(conn) == 0
    assert _claves(conn) == {"larga"}


def test_el_ttl_es_configurable(conn: psycopg.Connection) -> None:
    _clave(conn, ORG_A, "hace_3h", AHORA - timedelta(hours=3))
    _clave(conn, ORG_A, "hace_1h", AHORA - timedelta(hours=1))
    assert _purgar(conn, ttl=timedelta(hours=2)) == 1
    assert _claves(conn) == {"hace_1h"}


@pytest.mark.parametrize("ttl", [timedelta(0), timedelta(minutes=59), timedelta(hours=-1), None])
def test_un_ttl_menor_de_una_hora_se_rechaza_y_no_borra_nada(
    conn: psycopg.Connection, ttl: timedelta | None
) -> None:
    _clave(conn, ORG_A, "viva", AHORA)
    with (
        pytest.raises((errors.InvalidParameterValue, errors.NullValueNotAllowed)),
        conn.transaction(),
    ):
        conn.execute("SELECT core.purgar_idempotency_keys(%s, %s)", (AHORA, ttl))
    assert _claves(conn) == {"viva"}


def test_cada_purga_queda_registrada_con_su_recuento(conn: psycopg.Connection) -> None:
    _clave(conn, ORG_A, "v1", AHORA - timedelta(days=5))
    _clave(conn, ORG_B, "v2", AHORA - timedelta(days=5))
    _purgar(conn)
    fila = conn.execute(
        "SELECT filas FROM core.purgas_log WHERE tarea = 'idempotency_keys' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert fila is not None and fila[0] == 2


def test_purgar_dos_veces_es_inocuo(conn: psycopg.Connection) -> None:
    _clave(conn, ORG_A, "v", AHORA - timedelta(days=5))
    assert _purgar(conn) == 1
    assert _purgar(conn) == 0


def test_el_rol_worker_puede_purgar_pero_no_minimizar_y_la_app_no_puede_nada(
    conn: psycopg.Connection,
) -> None:
    _clave(conn, ORG_A, "v", AHORA - timedelta(days=5))
    with conn.transaction():
        conn.execute("SET LOCAL ROLE kallicode_worker")
        fila = conn.execute("SELECT core.purgar_idempotency_keys(%s)", (AHORA,)).fetchone()
        assert fila is not None and fila[0] == 1
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SET LOCAL ROLE kallicode_worker")
        conn.execute("SELECT * FROM core.minimizar_pii()")
    for rol in ("kallicode_app", "kallicode_worker"):
        with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
            conn.execute(f"SET LOCAL ROLE {rol}")
            conn.execute("SELECT count(*) FROM core.purgas_log")
    with pytest.raises(errors.InsufficientPrivilege), conn.transaction():
        conn.execute("SET LOCAL ROLE kallicode_app")
        conn.execute("SELECT core.purgar_idempotency_keys()")


def test_el_housekeeping_usa_la_funcion_y_no_un_ttl_propio() -> None:
    codigo = HOUSEKEEPING.read_text(encoding="utf-8")
    assert "core.purgar_idempotency_keys()" in codigo
    assert "DELETE FROM core.idempotency_keys" not in codigo


# ---------------------------------------------------------------------------
# Minimización de PII
# ---------------------------------------------------------------------------
def _token(
    conn: psycopg.Connection, tid: str, emitido: datetime, ip: str | None, ua: str | None
) -> None:
    conn.execute(
        "INSERT INTO core.refresh_tokens (id, tenant_id, user_id, token_hash, emitido_en, expira_en,"
        " ip, user_agent) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (tid, ORG_A, USUARIO[ORG_A], f"hash_{tid}", emitido, emitido + timedelta(days=60), ip, ua),
    )


def _invitacion(
    conn: psycopg.Connection, iid: str, estado: str, creada: datetime, email: str | None = None
) -> None:
    conn.execute(
        "INSERT INTO core.invitations (id, tenant_id, email, rol, token_hash, invitado_por, estado,"
        " expira_en, creado_en) VALUES (%s, %s, %s, 'member', %s, %s, %s, %s, %s)",
        (
            iid,
            ORG_A,
            email or f"{iid}@externo.dev",
            f"hash_{iid}",
            USUARIO[ORG_A],
            estado,
            creada + timedelta(days=7),
            creada,
        ),
    )


def _minimizar(conn: psycopg.Connection) -> dict[str, int]:
    filas = conn.execute("SELECT regla, filas FROM core.minimizar_pii(%s)", (AHORA,)).fetchall()
    return {r: int(n) for r, n in filas}


def test_ip_y_user_agent_se_anonimizan_solo_al_cumplir_la_ventana(
    conn: psycopg.Connection,
) -> None:
    _token(conn, "rt_vieja", AHORA - timedelta(days=31), "203.0.113.7", "Mozilla/5.0")
    _token(conn, "rt_reciente", AHORA - timedelta(days=29), "203.0.113.8", "Mozilla/5.0")
    _token(conn, "rt_solo_ip", AHORA - timedelta(days=45), "203.0.113.9", None)
    resultado = _minimizar(conn)
    assert resultado["refresh_tokens_red"] == 2
    filas = {
        r[0]: r[1:]
        for r in conn.execute(
            "SELECT id, host(ip), user_agent, token_hash FROM core.refresh_tokens "
            "WHERE id IN ('rt_vieja','rt_reciente','rt_solo_ip')"
        ).fetchall()
    }
    assert filas["rt_vieja"] == (None, None, "hash_rt_vieja")  # la fila y su hash siguen
    assert filas["rt_solo_ip"][0] is None
    assert filas["rt_reciente"][:2] == ("203.0.113.8", "Mozilla/5.0")


def test_el_email_de_invitaciones_cerradas_se_anonimiza_y_las_pendientes_no(
    conn: psycopg.Connection,
) -> None:
    vieja = AHORA - timedelta(days=91)
    _invitacion(conn, "inv_aceptada", "aceptada", vieja)
    _invitacion(conn, "inv_expirada", "expirada", vieja)
    _invitacion(conn, "inv_revocada", "revocada", vieja)
    _invitacion(conn, "inv_pendiente", "enviada", vieja)  # sigue en uso: no se toca
    _invitacion(conn, "inv_reciente", "aceptada", AHORA - timedelta(days=10))
    assert _minimizar(conn)["invitaciones_email"] == 3
    emails: dict[str, str] = dict(
        conn.execute("SELECT id, email FROM core.invitations WHERE id LIKE 'inv\\_%'").fetchall()
    )
    for iid in ("inv_aceptada", "inv_expirada", "inv_revocada"):
        assert emails[iid] == f"anonimizado-{iid}@anonimo.invalid"
    assert emails["inv_pendiente"] == "inv_pendiente@externo.dev"
    assert emails["inv_reciente"] == "inv_reciente@externo.dev"


def test_minimizar_es_idempotente_y_no_recuenta_lo_ya_anonimizado(
    conn: psycopg.Connection,
) -> None:
    _token(conn, "rt_vieja", AHORA - timedelta(days=40), "203.0.113.7", "UA")
    _invitacion(conn, "inv_vieja", "aceptada", AHORA - timedelta(days=100))
    assert _minimizar(conn) == {"refresh_tokens_red": 1, "invitaciones_email": 1}
    assert _minimizar(conn) == {"refresh_tokens_red": 0, "invitaciones_email": 0}


def test_una_regla_desactivada_no_se_ejecuta(conn: psycopg.Connection) -> None:
    _token(conn, "rt_vieja", AHORA - timedelta(days=40), "203.0.113.7", "UA")
    conn.execute("UPDATE core.pii_ventanas SET activa = false WHERE regla = 'refresh_tokens_red'")
    assert "refresh_tokens_red" not in _minimizar(conn)
    fila = conn.execute("SELECT host(ip) FROM core.refresh_tokens WHERE id = 'rt_vieja'").fetchone()
    assert fila is not None and fila[0] == "203.0.113.7"


def test_la_ventana_se_ajusta_sin_cambiar_codigo(conn: psycopg.Connection) -> None:
    _token(conn, "rt_10d", AHORA - timedelta(days=10), "203.0.113.7", "UA")
    assert _minimizar(conn)["refresh_tokens_red"] == 0
    conn.execute(
        "UPDATE core.pii_ventanas SET ventana = interval '7 days' WHERE regla = 'refresh_tokens_red'"
    )
    assert _minimizar(conn)["refresh_tokens_red"] == 1


def test_una_ventana_menor_de_una_hora_se_rechaza(conn: psycopg.Connection) -> None:
    with pytest.raises(errors.CheckViolation), conn.transaction():
        conn.execute(
            "UPDATE core.pii_ventanas SET ventana = interval '0' WHERE regla = 'refresh_tokens_red'"
        )


def test_cada_regla_deja_su_registro_para_el_informe_de_cumplimiento(
    conn: psycopg.Connection,
) -> None:
    _minimizar(conn)
    tareas = {
        r[0]
        for r in conn.execute(
            "SELECT tarea FROM core.purgas_log WHERE tarea LIKE 'pii:%'"
        ).fetchall()
    }
    assert {"pii:refresh_tokens_red", "pii:invitaciones_email"} <= tareas


def test_la_auditoria_sellada_por_hash_no_se_toca() -> None:
    """audit.audit_events lleva ip y user_agent dentro del sello: anonimizarlos rompería la cadena."""
    sql = MIGRACION.read_text(encoding="utf-8")
    cuerpo = sql.split("CREATE OR REPLACE FUNCTION core.minimizar_pii", 1)[1].split("$f$;", 2)[0]
    assert "audit." not in cuerpo


# ---------------------------------------------------------------------------
# Migración
# ---------------------------------------------------------------------------
def test_la_migracion_es_reejecutable_y_conserva_la_configuracion(conn: psycopg.Connection) -> None:
    conn.execute(
        "UPDATE core.pii_ventanas SET ventana = interval '45 days' WHERE regla = 'refresh_tokens_red'"
    )
    conn.execute(MIGRACION.read_text(encoding="utf-8"))
    conn.execute(MIGRACION.read_text(encoding="utf-8"))
    fila = conn.execute(
        "SELECT ventana FROM core.pii_ventanas WHERE regla = 'refresh_tokens_red'"
    ).fetchone()
    assert fila is not None and fila[0] == timedelta(days=45)  # no se pisa lo ya ajustado
