"""Servicio canónico de transiciones de estado para jobs del pipeline."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kallicode_core.errors import conflicto, no_encontrado
from kallicode_core.pipeline.estados import EstadoJob, es_transicion_valida


async def transicionar(
    db: AsyncSession,
    *,
    job_id: str,
    tenant_id: str,
    nuevo_estado: EstadoJob | str,
    motivo: str | None = None,
    contexto: dict[str, Any] | None = None,
    actor: str = "sistema",
) -> dict[str, Any]:
    """
    Ejecuta una transición de estado atómica sobre un job.

    Aplica bloqueo pesimista (SELECT ... FOR UPDATE) y valida
    la legalidad del salto contra la matriz canónica antes de mutar.
    Registra el evento en core.job_transitions en la misma transacción.
    """
    nuevo_estado_str = str(nuevo_estado)

    # 1. Bloqueo pesimista
    query_lock = text("""
        SELECT id, estado, tenant_id
        FROM core.jobs
        WHERE id = :job_id AND tenant_id = :tenant_id
        FOR UPDATE;
    """)
    res = await db.execute(query_lock, {"job_id": job_id, "tenant_id": tenant_id})
    job = res.mappings().first()

    if not job:
        raise no_encontrado(f"Job {job_id}")

    estado_actual = job["estado"]

    # 2. Validación de transición contra la máquina de estados canónica
    if not es_transicion_valida(estado_actual, nuevo_estado_str):
        raise conflicto(
            "TRANSICION_INVALIDA",
            f"Transición {estado_actual}→{nuevo_estado_str} no permitida.",
        )

    # 3. Actualizar estado del job
    query_update = text("""
        UPDATE core.jobs
        SET estado = :nuevo_estado,
            actualizado_en = now()
        WHERE id = :job_id AND tenant_id = :tenant_id;
    """)
    await db.execute(
        query_update,
        {
            "nuevo_estado": nuevo_estado_str,
            "job_id": job_id,
            "tenant_id": tenant_id,
        },
    )

    # 4. Registrar en historial de transiciones (append-only)
    query_historial = text("""
        INSERT INTO core.job_transitions (
            tenant_id,
            job_id,
            de_estado,
            a_estado,
            motivo,
            contexto,
            actor
        ) VALUES (
            :tenant_id,
            :job_id,
            :de_estado,
            :a_estado,
            :motivo,
            CAST(:contexto AS jsonb),
            :actor
        );
    """)
    await db.execute(
        query_historial,
        {
            "tenant_id": tenant_id,
            "job_id": job_id,
            "de_estado": estado_actual,
            "a_estado": nuevo_estado_str,
            "motivo": motivo,
            "contexto": json.dumps(contexto or {}),
            "actor": actor,
        },
    )

    return {
        "job_id": job_id,
        "de_estado": estado_actual,
        "a_estado": nuevo_estado_str,
    }
