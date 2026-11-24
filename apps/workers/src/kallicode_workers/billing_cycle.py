"""Renovación mensual del plan por aniversario (D13).

Corre cada hora; para las suscripciones activas con renueva_el <= hoy invoca
core.renovar_ciclo(tenant), que en una sola transacción (KC-DB-09-1, DB-F08):
  1. avanza renueva_el al próximo aniversario de inicia_el (meses cortos
     normalizados, sin arrastrar el día),
  2. abre el ciclo nuevo en usage_cycles (umbral normal) sin tocar el historial,
  3. desbloquea tickets en_cola_por_cuota y jobs pausado_por_cuota por
     prioridad y antigüedad, sin superar las líneas del plan,
  4. audita ciclo_renovado.
El worker notifica a los owners y crea el job de los tickets portal antiguos
que la renovación desbloqueó y que aún carecen de job.
"""

from __future__ import annotations

from kallicode_core.db import sesion_sistema, sesion_tenant, todos, uno
from kallicode_core.ids import nuevo
from kallicode_core.logging import log
from kallicode_core.pipeline.jobs import crear_job_inicial
from sqlalchemy import text


async def ciclo() -> None:
    async with sesion_sistema() as db:
        vencidas = await todos(
            db,
            """
            SELECT tenant_id FROM core.subscriptions
             WHERE finaliza_el IS NULL AND deleted_at IS NULL
               AND estado IN ('activa', 'periodo_gracia')
               AND renueva_el <= current_date
            FOR UPDATE SKIP LOCKED
            """,
        )
    for fila in vencidas:
        await _renovar(fila["tenant_id"])


async def _renovar(tenant: str) -> None:
    async with sesion_tenant(tenant, actor="svc:billing") as db:
        resultado = await uno(
            db,
            """
            SELECT renovado, ciclo_nuevo, proxima_renovacion,
                   tickets_desbloqueados, jobs_desbloqueados
              FROM core.renovar_ciclo(:t)
            """,
            {"t": tenant},
        )
        if not resultado or not resultado["renovado"]:
            log.debug("billing.ya_renovada", tenant_id=tenant)
            return

        desbloqueados = resultado["tickets_desbloqueados"] or []
        if desbloqueados:
            sin_job = await todos(
                db,
                """
                SELECT t.id, t.titulo, t.descripcion, t.prioridad
                  FROM core.tickets t
                 WHERE t.tenant_id = :t AND t.numero = ANY(:numeros)
                   AND t.origen = 'portal' AND t.etapa = 'triage'
                   AND NOT EXISTS (
                       SELECT 1 FROM core.jobs j WHERE j.ticket_id = t.id
                   )
                """,
                {"t": tenant, "numeros": desbloqueados},
            )
            for ticket in sin_job:
                await crear_job_inicial(
                    db,
                    tenant_id=tenant,
                    ticket_id=ticket["id"],
                    titulo=ticket["titulo"],
                    descripcion=ticket["descripcion"],
                    origen="portal",
                    prioridad=ticket["prioridad"],
                    pausado_por_cuota=False,
                )

        owners = await todos(
            db,
            """SELECT id FROM core.users
                WHERE tenant_id = :t AND rol = 'owner' AND estado = 'activo'""",
            {"t": tenant},
        )
        for o in owners:
            await db.execute(
                text("""
                INSERT INTO core.notifications (id, tenant_id, user_id, tipo, titulo,
                                                cuerpo)
                VALUES (:i, :t, :u, 'sistema', 'Ciclo renovado',
                        'Tu cuota mensual de LOC se ha renovado.')"""),
                {"i": nuevo("nt"), "t": tenant, "u": o["id"]},
            )

    log.info(
        "billing.renovado",
        tenant_id=tenant,
        ciclo=resultado["ciclo_nuevo"],
        desbloqueados=len(desbloqueados),
        jobs=resultado["jobs_desbloqueados"],
    )
