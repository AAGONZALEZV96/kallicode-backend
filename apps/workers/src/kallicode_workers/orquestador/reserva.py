"""Reserva de jobs para el orquestador.

Toma el siguiente job pendiente con FOR UPDATE SKIP LOCKED y, en la MISMA
transacción, lo transiciona a 'triage' para que dos réplicas concurrentes
nunca vean el mismo job. El orden es por prioridad (columna real `prioridad`)
y luego por antigüedad (`creado_en`).

Nota sobre el rol: la reserva usa `sesion_sistema()` (rol administrativo con
BYPASSRLS) en lugar de `sesion_worker()`. Motivo: el UPDATE de estado dispara
el trigger `trg_jobs_transicion`, que inserta en `core.job_transitions`, y esa
tabla no tiene política RLS para el rol `kallicode_worker`. Es un pendiente
del Equipo 2 (Database) revisar si `job_transitions` debería incluirse en la
política del worker o si el orquestador debe operar siempre con rol admin.
"""

from __future__ import annotations

from typing import Any

from kallicode_core.db import sesion_sistema, uno
from kallicode_core.logging import log
from sqlalchemy import text

# Sprint 4: solo reservamos jobs recién creados. Los jobs que ya están en
# 'triage' (a mitad de ejecución) no se re-reservan aquí; el manejo de
# reintentos a mitad de etapa llega en el Sprint 6.
_ESTADOS_RESERVABLES = ("pendiente_asignacion",)


async def reservar_siguiente() -> dict[str, Any] | None:
    """Reserva el siguiente job elegible. Devuelve None si no hay trabajo.

    La reserva hace dos cosas en la MISMA transacción:
      1. `SELECT ... FOR UPDATE SKIP LOCKED` para tomar el siguiente job.
      2. `UPDATE ... SET estado='triage'` para marcarlo como reservado.

    Así dos réplicas concurrentes nunca ven el mismo job: la primera lo
    transiciona fuera de `_ESTADOS_RESERVABLES` y la segunda ya no lo
    encuentra en el SELECT.

    Al salir del `async with`, la transacción cierra y el lock se libera,
    pero la transición ya está persistida.
    """
    estados = list(_ESTADOS_RESERVABLES)
    async with sesion_sistema() as db:
        fila = await uno(
            db,
            """
                SELECT id, tenant_id, ticket_id, estado, prioridad, contexto, iteraciones
                FROM core.jobs
                WHERE estado = ANY(:estados)
                ORDER BY
                    CASE prioridad
                        WHEN 'P1' THEN 1
                        WHEN 'P2' THEN 2
                        WHEN 'P3' THEN 3
                        WHEN 'P4' THEN 4
                        ELSE 5
                    END ASC,
                    creado_en ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            """,
            {"estados": estados},
        )
        if fila is None:
            return None

        # Transiciona a 'triage' en la misma transacción para que el job
        # deje de ser reservable por otras réplicas.
        await db.execute(
            text(
                """
                UPDATE core.jobs
                SET estado = 'triage',
                    actualizado_en = now()
                WHERE id = :id AND tenant_id = :t
                """
            ),
            {"id": fila["id"], "t": fila["tenant_id"]},
        )

        log.debug(
            "orquestador.job_reservado",
            job_id=fila["id"],
            tenant_id=fila["tenant_id"],
            estado_previo=fila["estado"],
        )

        # El dict que devolvemos refleja el estado ya transicionado.
        return {**dict(fila), "estado": "triage"}
