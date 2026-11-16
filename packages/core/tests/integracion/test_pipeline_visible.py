"""El pipeline del cliente coincide con la base (KC-BE-06-5, cierra BE-F42).

kallicode_core.pipeline.vista debe seguir a sus dos fuentes de verdad:
  - core.pipeline_stages (migración 049): las etapas y su orden.
  - El trigger T9 (core.mirror_ticket_stage): qué etapa y qué gate ve el ticket para
    cada estado del job.
Todo ocurre en una transacción que se deshace.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import get_args

import psycopg
import pytest
from kallicode_core.pipeline import (
    ETAPAS_FLUJO,
    EstadoJob,
    EtapaTicket,
    etapa_visible,
    mapear_pipeline,
)
from kallicode_core.pipeline.estados import GATES
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integracion

TENANT = "t_be065"
TICKET = "tk_be065"
JOB = "jb_be065"

# Estados de escape: la etapa del ticket la decide el trigger de otra forma.
ESCAPES = {
    EstadoJob.PAUSADO_POR_CUOTA: "en_cola_por_cuota",
    EstadoJob.CANCELADO: "cancelado",
    EstadoJob.CERRADO_DUPLICADO: "cerrado_duplicado",
}


def _normalizar_dsn(url: str) -> str:
    esquema, sep, resto = url.partition("://")
    return f"{esquema.split('+', 1)[0]}://{resto}" if sep else url


def _etapas_ticket() -> set[str]:
    return {valor for literal in get_args(EtapaTicket) for valor in get_args(literal)}


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
        yield c
    finally:
        c.rollback()
        c.close()


@pytest.fixture
def job(db: psycopg.Connection) -> psycopg.Connection:
    db.execute(
        "INSERT INTO core.organizations (id, nombre) VALUES (%s, 'Org KC-BE-06-5')", [TENANT]
    )
    db.execute(
        """INSERT INTO core.tickets (id, tenant_id, numero, tipo, titulo, descripcion)
           VALUES (%s, %s, 'KC-9065', 'bug', 'Pipeline visible', 'Prueba de KC-BE-06-5')""",
        [TICKET, TENANT],
    )
    db.execute(
        """INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad, iteraciones)
           VALUES (%s, %s, %s, 'triage', 'P3', %s)""",
        [JOB, TENANT, TICKET, Jsonb({})],
    )
    return db


def test_las_etapas_son_las_de_pipeline_stages_en_su_orden(db: psycopg.Connection) -> None:
    codigos = [
        fila[0]
        for fila in db.execute(
            "SELECT codigo FROM core.pipeline_stages ORDER BY orden_flujo"
        ).fetchall()
    ]

    assert [c for c in codigos if c in ETAPAS_FLUJO] == list(ETAPAS_FLUJO)
    # Cada etapa posible del ticket está tipada y tiene un pipeline.
    assert set(codigos) == _etapas_ticket()
    for codigo in codigos:
        assert len(mapear_pipeline(codigo, None)) == len(ETAPAS_FLUJO)


@pytest.mark.parametrize("estado", [e for e in EstadoJob if e is not EstadoJob.ESCALADO_HUMANO])
def test_el_trigger_t9_muestra_la_etapa_y_el_gate_de_la_vista(
    job: psycopg.Connection, estado: EstadoJob
) -> None:
    if estado is EstadoJob.CANCELADO:
        # tk_cancel_chk: como tickets.actualizar, el motivo se guarda antes de cancelar.
        job.execute(
            "UPDATE core.tickets SET motivo_cancelacion = %s WHERE id = %s", ["Prueba", TICKET]
        )
    job.execute("UPDATE core.jobs SET estado = %s WHERE id = %s", [estado.value, JOB])

    fila = job.execute(
        "SELECT etapa, gate_pendiente FROM core.tickets WHERE id = %s", [TICKET]
    ).fetchone()
    assert fila is not None
    etapa, gate = fila

    assert etapa == (ESCAPES.get(estado) or etapa_visible(estado))
    assert gate == GATES.get(estado)
    # El stepper de esa etapa se arma sin error.
    mapear_pipeline(etapa, gate, estado)
