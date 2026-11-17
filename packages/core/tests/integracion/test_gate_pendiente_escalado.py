"""gate_pendiente al escalar a una persona (KC-BE-06-3, migración 063).

El espejo ticket ⇄ job (core.mirror_ticket_stage) debe limpiar tickets.gate_pendiente
cuando el job pasa a escalado_humano, sin cambiar la etapa visible, y volver a
mostrarlo si el job regresa a un gate. Todo ocurre en una transacción que se deshace.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integracion

TENANT = "t_be063"
TICKET = "tk_be063"
JOB = "jb_be063"


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


@pytest.fixture
def db() -> Iterator[psycopg.Connection]:
    url = os.getenv("KC_DATABASE_URL_MIGRATIONS") or os.getenv("KC_DATABASE_URL_ADMIN")
    if not url:
        pytest.skip("KC_DATABASE_URL_MIGRATIONS no definida")
    try:
        c = psycopg.connect(_normalizar_dsn(url), autocommit=False)
    except psycopg.OperationalError as exc:
        pytest.skip(f"Base de datos no disponible: {exc}")
    try:
        c.execute(
            "INSERT INTO core.organizations (id, nombre) VALUES (%s, 'Org KC-BE-06-3')", [TENANT]
        )
        c.execute(
            """INSERT INTO core.tickets (id, tenant_id, numero, tipo, titulo, descripcion)
               VALUES (%s, %s, 'KC-9063', 'bug', 'Gate escalado', 'Prueba de la migración 063')""",
            [TICKET, TENANT],
        )
        c.execute(
            """INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad, iteraciones)
               VALUES (%s, %s, %s, 'design', 'P3', %s)""",
            [JOB, TENANT, TICKET, Jsonb({"design": 1})],
        )
        yield c
    finally:
        c.rollback()
        c.close()


def _job_a(c: psycopg.Connection, estado: str) -> None:
    c.execute("UPDATE core.jobs SET estado = %s WHERE id = %s", [estado, JOB])


def _ticket(c: psycopg.Connection) -> tuple[str, int | None]:
    fila = c.execute("SELECT etapa, gate_pendiente FROM core.tickets WHERE id = %s", [TICKET])
    resultado = fila.fetchone()
    assert resultado is not None
    return resultado[0], resultado[1]


@pytest.mark.parametrize(
    ("gate", "etapa"),
    [("gate1", "design"), ("gate2", "qa"), ("gate3", "deploy")],
)
def test_escalar_desde_un_gate_limpia_gate_pendiente_y_conserva_la_etapa(
    db: psycopg.Connection, gate: str, etapa: str
) -> None:
    _job_a(db, gate)
    assert _ticket(db) == (etapa, int(gate[-1]))

    _job_a(db, "escalado_humano")

    assert _ticket(db) == (etapa, None)


def test_al_volver_del_escalado_a_un_gate_lo_vuelve_a_mostrar(db: psycopg.Connection) -> None:
    _job_a(db, "gate1")
    _job_a(db, "escalado_humano")

    _job_a(db, "design")
    _job_a(db, "gate1")

    assert _ticket(db) == ("design", 1)


def test_escalar_fuera_de_un_gate_no_cambia_el_ticket(db: psycopg.Connection) -> None:
    _job_a(db, "build")
    assert _ticket(db) == ("build", None)

    _job_a(db, "escalado_humano")

    assert _ticket(db) == ("build", None)


def test_los_demas_estados_siguen_igual(db: psycopg.Connection) -> None:
    _job_a(db, "pausado_por_cuota")
    assert _ticket(db) == ("en_cola_por_cuota", None)

    _job_a(db, "deploy_preflight")
    assert _ticket(db) == ("deploy", None)
