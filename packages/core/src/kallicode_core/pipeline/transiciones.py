"""Servicio canónico de transiciones de estado para jobs del pipeline."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from kallicode_core.errors import conflicto, no_encontrado
from kallicode_core.pipeline.estados import EstadoJob, es_transicion_valida

ETAPAS_CON_ITERACION = {"design", "build", "qa"}


async def transicionar(
    db: AsyncSession,
    *,
    job_id: str,
    tenant_id: str,
    nuevo_estado: EstadoJob | str,
    motivo: str | None = None,
    contexto: dict[str, Any] | None = None,
    actor: str = "sistema",
    incrementar_iteracion: bool | None = None,
) -> dict[str, Any]:
    """
    Ejecuta una transición de estado atómica sobre un job.

    Aplica bloqueo pesimista (SELECT ... FOR UPDATE), valida
    la legalidad del salto contra la matriz canónica, actualiza
    el contador de iteraciones de forma persistente y registra
    la auditoría completa en core.job_transitions.
    """
    nuevo_estado_str = str(nuevo_estado)

    # 1. Bloqueo pesimista y lectura de estado e iteraciones actuales
    query_lock = text("""
        SELECT id, estado, tenant_id, iteraciones
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

    # 3. Cálculo del contador de iteraciones (defensivo ante mocks y columnas nulas)
    iteraciones_raw = None
    try:
        iteraciones_raw = job["iteraciones"]
    except (KeyError, IndexError):
        iteraciones_raw = getattr(job, "iteraciones", None)

    iteraciones = dict(iteraciones_raw or {})

    # Se incrementa si se solicita explícitamente o si es una fase cíclica con motivo
    debe_incrementar = (
        incrementar_iteracion
        if incrementar_iteracion is not None
        else (nuevo_estado_str in ETAPAS_CON_ITERACION and bool(motivo))
    )

    if debe_incrementar:
        iteraciones[nuevo_estado_str] = iteraciones.get(nuevo_estado_str, 0) + 1

    iteracion_actual = iteraciones.get(nuevo_estado_str, 0)

    # 4. Actualizar estado e iteraciones en core.jobs
    query_update = text("""
        UPDATE core.jobs
        SET estado = :nuevo_estado,
            iteraciones = CAST(:iteraciones AS jsonb),
            actualizado_en = now()
        WHERE id = :job_id AND tenant_id = :tenant_id;
    """)
    await db.execute(
        query_update,
        {
            "nuevo_estado": nuevo_estado_str,
            "iteraciones": json.dumps(iteraciones),
            "job_id": job_id,
            "tenant_id": tenant_id,
        },
    )

    # 5. Registrar en historial de transiciones (core.job_transitions)
    ctx = dict(contexto or {})
    ctx.setdefault("iteracion", iteracion_actual)

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
            "contexto": json.dumps(ctx),
            "actor": actor,
        },
    )

    return {
        "job_id": job_id,
        "de_estado": estado_actual,
        "a_estado": nuevo_estado_str,
        "iteracion": iteracion_actual,
        "iteraciones": iteraciones,
    }
