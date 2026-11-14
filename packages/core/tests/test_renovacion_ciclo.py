"""KC-DB-09-1 (DB-F08): renovación por aniversario y desbloqueo de cuota.

Cubre core.renovar_ciclo, core.proximo_aniversario, core.desbloquear_cola_cuota y el
chequeo V23 de core.validar_integridad() (migración 051_renovacion_ciclo.sql):

- idempotencia: dos invocaciones el mismo día → un ciclo y un evento ciclo_renovado;
- meses cortos: contratos del 29, 30 y 31 contra febrero no bisiesto, sin arrastre;
- saldo histórico: el ciclo anterior queda intacto y el nuevo arranca en 'normal';
- desbloqueo: cinco tickets en cola y un plan de tres líneas, por prioridad y antigüedad;
- bloqueo concurrente: dos transacciones simultáneas renuevan una sola vez;
- V23 cuenta la suscripción vencida y deja de contarla tras renovar.

Requiere PostgreSQL real con las migraciones aplicadas; sin BD se omite.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row


def _dsn() -> str:
    url = (
        os.getenv("KC_DATABASE_URL_ADMIN")
        or os.getenv("KC_DATABASE_URL_MIGRATIONS")
        or os.getenv("DATABASE_URL")
        or "postgresql://kallicode_admin:KalliCode_Dev2026!@127.0.0.1:5432/kallicode_test"
    )
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _conectar(autocommit: bool = True) -> psycopg.Connection:
    try:
        return psycopg.connect(_dsn(), autocommit=autocommit)
    except psycopg.Error as exc:
        pytest.skip(f"Base de datos no accesible en este entorno: {exc}")


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    c = _conectar()
    yield c
    c.close()


@pytest.fixture
def tenants(conn: psycopg.Connection) -> Iterator[list[str]]:
    """Lista donde cada test registra sus tenants; se suprimen al terminar."""
    creados: list[str] = []
    yield creados
    with conn.cursor() as cur:
        for t in creados:
            cur.execute("SELECT core.suprimir_tenant(%s, 'test', 'test_renovacion')", (t,))


def _crear_tenant(
    conn: psycopg.Connection,
    tenants: list[str],
    inicia_el: date,
    renueva_el: date,
    plan: str = "growth",
) -> str:
    tenant = f"org_ren_{uuid.uuid4().hex[:10]}"
    tenants.append(tenant)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, 'Tenant renovación')",
            (tenant,),
        )
        cur.execute(
            """
            INSERT INTO core.subscriptions (id, tenant_id, plan_codigo, estado, inicia_el, renueva_el)
            VALUES (%s, %s, %s, 'activa', %s, %s)
            """,
            (f"sub_{uuid.uuid4().hex[:10]}", tenant, plan, inicia_el, renueva_el),
        )
    return tenant


def _renovar(conn: psycopg.Connection, tenant: str, hoy: date) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT * FROM core.renovar_ciclo(%s, %s)", (tenant, hoy))
        fila = cur.fetchone()
    assert fila is not None
    return fila


def _escalar(conn: psycopg.Connection, sql: str, params: tuple = ()) -> object:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        fila = cur.fetchone()
    assert fila is not None
    return fila[0]


# --------------------------------------------------------------- meses cortos
@pytest.mark.parametrize("dia", [29, 30, 31])
def test_proximo_aniversario_normaliza_febrero_sin_arrastre(
    conn: psycopg.Connection, dia: int
) -> None:
    inicia = date(2026, 1, dia)
    feb = _escalar(conn, "SELECT core.proximo_aniversario(%s, %s)", (inicia, date(2027, 1, dia)))
    assert feb == date(2027, 2, 28)  # 2027 no es bisiesto
    mar = _escalar(conn, "SELECT core.proximo_aniversario(%s, %s)", (inicia, feb))
    assert mar == date(2027, 3, dia)  # vuelve al día contratado: no arrastra el 28
    bisiesto = _escalar(
        conn, "SELECT core.proximo_aniversario(%s, %s)", (inicia, date(2028, 1, dia))
    )
    assert bisiesto == date(2028, 2, 29)


@pytest.mark.parametrize("dia", [29, 30, 31])
def test_renovar_ciclo_meses_cortos(conn: psycopg.Connection, tenants: list[str], dia: int) -> None:
    tenant = _crear_tenant(conn, tenants, date(2026, 1, dia), date(2027, 1, dia))

    enero = _renovar(conn, tenant, date(2027, 1, dia))
    assert enero["renovado"] is True
    assert enero["ciclo_nuevo"] == "2027-01"
    assert enero["proxima_renovacion"] == date(2027, 2, 28)

    febrero = _renovar(conn, tenant, date(2027, 2, 28))
    assert febrero["renovado"] is True
    assert febrero["ciclo_nuevo"] == "2027-02"
    assert febrero["proxima_renovacion"] == date(2027, 3, dia)


# --------------------------------------------------------------- idempotencia
def test_renovar_ciclo_idempotente_mismo_dia(conn: psycopg.Connection, tenants: list[str]) -> None:
    tenant = _crear_tenant(conn, tenants, date(2026, 3, 10), date(2026, 11, 10))
    hoy = date(2026, 11, 14)

    primera = _renovar(conn, tenant, hoy)
    segunda = _renovar(conn, tenant, hoy)

    assert primera["renovado"] is True
    assert primera["ciclo_nuevo"] == "2026-11"
    assert primera["proxima_renovacion"] == date(2026, 12, 10)
    assert segunda["renovado"] is False
    assert segunda["ciclo_nuevo"] == "2026-11"
    assert segunda["proxima_renovacion"] == date(2026, 12, 10)

    ciclos = _escalar(
        conn, "SELECT count(*) FROM core.usage_cycles WHERE tenant_id = %s", (tenant,)
    )
    eventos = _escalar(
        conn,
        "SELECT count(*) FROM audit.audit_events WHERE tenant_id = %s AND evento = 'ciclo_renovado'",
        (tenant,),
    )
    assert ciclos == 1
    assert eventos == 1


def test_renovar_ciclo_sin_suscripcion_no_hace_nada(conn: psycopg.Connection) -> None:
    resultado = _renovar(conn, f"org_inexistente_{uuid.uuid4().hex[:8]}", date(2026, 11, 14))
    assert resultado["renovado"] is False
    assert resultado["tickets_desbloqueados"] == []


# ------------------------------------------------------------ saldo histórico
def test_renovar_ciclo_preserva_historial(conn: psycopg.Connection, tenants: list[str]) -> None:
    tenant = _crear_tenant(conn, tenants, date(2026, 3, 10), date(2026, 11, 10))
    with conn.cursor() as cur:
        # Consumo del ciclo anterior (2026-10) a través de usage_loc: lo agrega el trigger.
        cur.execute(
            """
            INSERT INTO core.usage_loc (tenant_id, ciclo, tipo, netas, motivo_ajuste)
            VALUES (%s, '2026-10', 'ajuste', 8000, 'consumo del ciclo anterior')
            """,
            (tenant,),
        )

    _renovar(conn, tenant, date(2026, 11, 14))

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ciclo, loc_consumidas, umbral FROM core.usage_cycles
             WHERE tenant_id = %s ORDER BY ciclo
            """,
            (tenant,),
        )
        filas = cur.fetchall()
    assert filas == [("2026-10", 8000, "agotado"), ("2026-11", 0, "normal")]


# ------------------------------------------------------------------ desbloqueo
def _ticket(
    conn: psycopg.Connection,
    tenant: str,
    numero: str,
    prioridad: str,
    creado_en: str,
    job_prioridad: str | None = None,
    job_estado: str = "pausado_por_cuota",
) -> str:
    ticket_id = f"tk_{uuid.uuid4().hex[:10]}"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO core.tickets (id, tenant_id, numero, tipo, titulo, descripcion,
                                      prioridad, etapa, creado_en)
            VALUES (%s, %s, %s, 'bug', %s, 'desc', %s, 'en_cola_por_cuota', %s)
            """,
            (ticket_id, tenant, numero, numero, prioridad, creado_en),
        )
        if job_prioridad is not None:
            cur.execute(
                """
                INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (f"job_{uuid.uuid4().hex[:10]}", tenant, ticket_id, job_estado, job_prioridad),
            )
    return ticket_id


def _etapas(conn: psycopg.Connection, tenant: str) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT numero, etapa FROM core.tickets WHERE tenant_id = %s", (tenant,))
        return dict(cur.fetchall())


def test_desbloqueo_respeta_prioridad_antiguedad_y_lineas(
    conn: psycopg.Connection, tenants: list[str]
) -> None:
    # Plan growth: 3 líneas. Cinco tickets en cola; dos con job pausado.
    tenant = _crear_tenant(conn, tenants, date(2026, 3, 10), date(2026, 11, 10))
    _ticket(conn, tenant, "KC-1", "baja", "2026-10-01")
    _ticket(conn, tenant, "KC-2", "alta", "2026-10-05")
    _ticket(conn, tenant, "KC-3", "media", "2026-10-09", job_prioridad="P1")
    _ticket(conn, tenant, "KC-4", "media", "2026-09-01", job_prioridad="P3")
    _ticket(conn, tenant, "KC-5", "alta", "2026-10-02")

    resultado = _renovar(conn, tenant, date(2026, 11, 14))

    # P1 primero; después las 'alta' (= P2) de la más antigua a la más nueva.
    assert resultado["tickets_desbloqueados"] == ["KC-3", "KC-5", "KC-2"]
    assert resultado["jobs_desbloqueados"] == 1
    assert _etapas(conn, tenant) == {
        "KC-1": "en_cola_por_cuota",
        "KC-2": "triage",
        "KC-3": "triage",  # vía el job (trigger T9)
        "KC-4": "en_cola_por_cuota",
        "KC-5": "triage",
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.numero, j.estado FROM core.jobs j JOIN core.tickets t ON t.id = j.ticket_id
             WHERE j.tenant_id = %s ORDER BY t.numero
            """,
            (tenant,),
        )
        assert cur.fetchall() == [("KC-3", "pendiente_asignacion"), ("KC-4", "pausado_por_cuota")]


def test_desbloqueo_descuenta_lineas_ocupadas(conn: psycopg.Connection, tenants: list[str]) -> None:
    tenant = _crear_tenant(conn, tenants, date(2026, 3, 10), date(2026, 11, 10))
    # Un job activo (fuera de la cola) ya ocupa una de las 3 líneas.
    activo = _ticket(
        conn, tenant, "KC-9", "media", "2026-08-01", job_prioridad="P2", job_estado="build"
    )
    assert activo
    for n in range(1, 6):
        _ticket(conn, tenant, f"KC-{n}", "media", f"2026-10-0{n}")

    resultado = _renovar(conn, tenant, date(2026, 11, 14))

    assert resultado["tickets_desbloqueados"] == ["KC-1", "KC-2"]


# ------------------------------------------------------------------ concurrencia
def test_renovaciones_concurrentes_renuevan_una_vez(
    conn: psycopg.Connection, tenants: list[str]
) -> None:
    tenant = _crear_tenant(conn, tenants, date(2026, 3, 10), date(2026, 11, 10))
    hoy = date(2026, 11, 14)
    c1 = _conectar(autocommit=False)
    c2 = _conectar(autocommit=False)
    resultados: dict[str, dict] = {}

    def segunda() -> None:
        resultados["c2"] = _renovar(c2, tenant, hoy)
        c2.commit()

    try:
        resultados["c1"] = _renovar(c1, tenant, hoy)  # toma el candado y no confirma aún
        hilo = threading.Thread(target=segunda)
        hilo.start()
        hilo.join(timeout=1.0)
        assert hilo.is_alive(), "la segunda renovación no esperó al candado de la suscripción"
        c1.commit()
        hilo.join(timeout=10.0)
        assert not hilo.is_alive()
    finally:
        c1.close()
        c2.close()

    assert resultados["c1"]["renovado"] is True
    assert resultados["c2"]["renovado"] is False
    assert resultados["c2"]["proxima_renovacion"] == date(2026, 12, 10)
    eventos = _escalar(
        conn,
        "SELECT count(*) FROM audit.audit_events WHERE tenant_id = %s AND evento = 'ciclo_renovado'",
        (tenant,),
    )
    assert eventos == 1


# --------------------------------------------------------------------------- V23
def _v23(conn: psycopg.Connection) -> int:
    valor = _escalar(
        conn,
        "SELECT problemas FROM core.validar_integridad() WHERE chequeo = 'V23_renovacion_vencida'",
    )
    assert isinstance(valor, int)
    return valor


def test_v23_cuenta_renovaciones_vencidas(conn: psycopg.Connection, tenants: list[str]) -> None:
    hoy = date.today()
    antes = _v23(conn)
    tenant = _crear_tenant(
        conn, tenants, date(hoy.year - 1, hoy.month, 1), date.fromordinal(hoy.toordinal() - 1)
    )
    assert _v23(conn) == antes + 1

    _renovar(conn, tenant, hoy)

    assert _v23(conn) == antes
