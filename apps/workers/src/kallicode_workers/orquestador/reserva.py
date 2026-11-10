"""Reserva jobs ejecutables para el orquestador.

Selecciona jobs en triage, design o build con FOR UPDATE SKIP LOCKED; los
nuevos pasan a triage en la misma transacción. El orden es por prioridad
(columna real `prioridad`) y luego por antigüedad (`creado_en`).

Nota sobre el rol: la reserva usa `sesion_sistema()` (rol administrativo con
BYPASSRLS) en lugar de `sesion_worker()`. Motivo: el UPDATE de estado dispara
el trigger `trg_jobs_transicion`, que inserta en `core.job_transitions`, y esa
tabla no tiene política RLS para el rol `kallicode_worker`. Es un pendiente
del Equipo 2 (Database) revisar si `job_transitions` debería incluirse en la
política del worker o si el orquestador debe operar siempre con rol admin.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from kallicode_core.db import conexion_sistema, sesion_sistema, todos, uno
from kallicode_core.logging import log
from sqlalchemy import text

# Solo se reservan estados que tienen una etapa ejecutable. Los estados de
# gate esperan una firma humana y no deben volver a entrar al orquestador.
_ESTADOS_RESERVABLES = ("pendiente_asignacion", "triage", "design", "build")
_MAX_CANDIDATOS = 100


@asynccontextmanager
async def reservar_siguiente() -> AsyncIterator[dict[str, Any] | None]:
    """Reserva el siguiente job elegible hasta salir del contexto.

    Un advisory lock de sesión evita que otra réplica ejecute el mismo job
    durante las llamadas externas. La fila se bloquea solo durante la lectura
    y transición inicial, para no bloquear las transiciones de la API.

    Uso: `async with reservar_siguiente() as job: ...`
    """
    async with conexion_sistema() as lock_conn:
        async with sesion_sistema() as db:
            candidatos = await todos(
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
                LIMIT :limite
            """,
                {"estados": list(_ESTADOS_RESERVABLES), "limite": _MAX_CANDIDATOS},
            )

        for candidato in candidatos:
            async with lock_conn.begin():
                adquirido = await lock_conn.scalar(
                    text("SELECT pg_try_advisory_lock(hashtextextended(:id, 0))"),
                    {"id": f"kallicode:orquestador:{candidato['id']}"},
                )
            if not adquirido:
                continue

            try:
                async with sesion_sistema() as db:
                    fila = await uno(
                        db,
                        """
                        SELECT id, tenant_id, ticket_id, estado, prioridad, contexto, iteraciones
                        FROM core.jobs
                        WHERE id = :id AND estado = ANY(:estados)
                        FOR UPDATE SKIP LOCKED
                        """,
                        {
                            "id": candidato["id"],
                            "estados": list(_ESTADOS_RESERVABLES),
                        },
                    )
                    if fila is None:
                        continue

                    estado_previo = fila["estado"]
                    if estado_previo == "pendiente_asignacion":
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
                    estado_previo=estado_previo,
                )

                estado = "triage" if estado_previo == "pendiente_asignacion" else estado_previo
                yield {**dict(fila), "estado": estado}
                return
            finally:
                try:
                    async with lock_conn.begin():
                        liberado = await lock_conn.scalar(
                            text("SELECT pg_advisory_unlock(hashtextextended(:id, 0))"),
                            {"id": f"kallicode:orquestador:{candidato['id']}"},
                        )
                    if not liberado:
                        raise RuntimeError(f"No se pudo liberar el lock del job {candidato['id']}")
                except Exception:
                    await lock_conn.invalidate()
                    raise

        yield None


async def registrar_intento_fallido(job_id: str, tenant_id: str, estado: str) -> None:
    """Persiste el intento fallido para que el siguiente ciclo pueda reintentar."""
    async with sesion_sistema() as db:
        await db.execute(
            text(
                """
                UPDATE core.jobs
                   SET iteraciones = jsonb_set(
                       COALESCE(iteraciones, '{}'::jsonb),
                       ARRAY[:estado],
                       to_jsonb(COALESCE((iteraciones ->> :estado)::integer, 0) + 1),
                       true
                   ),
                       actualizado_en = now()
                 WHERE id = :id AND tenant_id = :tenant_id
                """
            ),
            {"estado": estado, "id": job_id, "tenant_id": tenant_id},
        )
