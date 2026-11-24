"""Creación del job inicial de un ticket dentro de la transacción del tenant."""

from __future__ import annotations

import json

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..ids import nuevo
from .estados import EstadoJob

_PRIORIDAD_TICKET_A_JOB = {"alta": "P1", "media": "P2", "baja": "P3"}


async def crear_job_inicial(
    db: AsyncSession,
    *,
    tenant_id: str,
    ticket_id: str,
    titulo: str,
    descripcion: str,
    origen: str,
    prioridad: str,
    pausado_por_cuota: bool,
) -> str:
    job_id = nuevo("jb")
    estado = EstadoJob.PAUSADO_POR_CUOTA if pausado_por_cuota else EstadoJob.PENDIENTE_ASIGNACION
    await db.execute(
        text("""
            INSERT INTO core.jobs (id, tenant_id, ticket_id, estado, prioridad, contexto)
            VALUES (:id, :t, :tk, :estado, :prioridad, CAST(:ctx AS jsonb))
        """),
        {
            "id": job_id,
            "t": tenant_id,
            "tk": ticket_id,
            "estado": estado.value,
            "prioridad": _PRIORIDAD_TICKET_A_JOB.get(prioridad, "P2"),
            "ctx": json.dumps({"titulo": titulo, "descripcion": descripcion, "origen": origen}),
        },
    )
    return job_id
