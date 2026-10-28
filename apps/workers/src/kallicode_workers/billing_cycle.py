"""Renovación mensual del plan por aniversario (D13).

Corre cada hora; para las suscripciones con renueva_el <= hoy:
  1. avanza renueva_el un mes (normalizando meses cortos),
  2. crea el registro del nuevo ciclo en usage_cycles (umbral normal),
  3. desbloquea tickets en_cola_por_cuota y jobs pausado_por_cuota,
  4. audita ciclo_renovado y notifica al owner.

KC-BE-04-5 (cierra BE-F18):
  - El UPDATE lleva la condición `renueva_el <= current_date` en el propio
    WHERE y `RETURNING`, para que sea atómico e idempotente aunque se llame
    dos veces o sin líder.
  - `encolados` se inicializa FUERA del `async with` para que un aborto
    del bloque no produzca NameError al hacer el xadd.
"""

from __future__ import annotations

from kallicode_core.auditoria import registrar_evento
from kallicode_core.comercial import ciclo_actual, redis_cliente
from kallicode_core.db import sesion_sistema, sesion_tenant, todos, uno
from kallicode_core.ids import nuevo
from kallicode_core.logging import log
from sqlalchemy import text


async def ciclo() -> None:
    async with sesion_sistema() as db:
        vencidas = await todos(
            db,
            """
            SELECT tenant_id FROM core.subscriptions
             WHERE finaliza_el IS NULL AND renueva_el <= current_date
            FOR UPDATE SKIP LOCKED
            """,
        )
    for fila in vencidas:
        await _renovar(fila["tenant_id"])


async def _renovar(tenant: str) -> None:
    # FIX BE-F18: inicializar FUERA del bloque. Antes `encolados` se
    # asignaba dentro del `async with` y se consumía fuera (xadd); si el
    # bloque abortaba antes de asignarla, saltaba NameError.
    encolados: list[dict] = []

    async with sesion_tenant(tenant, actor="svc:billing") as db:
        # FIX BE-F18: condición `renueva_el <= current_date` en el propio
        # UPDATE + RETURNING. Sin esto, dos réplicas (o dos invocaciones)
        # avanzaban el aniversario dos meses.
        actualizado = await uno(
            db,
            """
            UPDATE core.subscriptions
               SET renueva_el = (renueva_el + interval '1 month')::date
             WHERE tenant_id = :t
               AND finaliza_el IS NULL
               AND renueva_el <= current_date
            RETURNING renueva_el
            """,
            {"t": tenant},
        )
        if not actualizado:
            log.debug("billing.ya_renovada", tenant_id=tenant)
            return

        await db.execute(
            text("""
            INSERT INTO core.usage_cycles (tenant_id, ciclo)
            VALUES (:t, :c)
            ON CONFLICT (tenant_id, ciclo) DO NOTHING
            """),
            {"t": tenant, "c": ciclo_actual()},
        )

        encolados = await todos(
            db,
            """
            UPDATE core.tickets SET etapa = 'triage'
             WHERE tenant_id = :t AND etapa = 'en_cola_por_cuota'
             RETURNING numero
            """,
            {"t": tenant},
        )
        await db.execute(
            text("""
            UPDATE core.jobs SET estado = 'pendiente_asignacion'
             WHERE tenant_id = :t AND estado = 'pausado_por_cuota'"""),
            {"t": tenant},
        )
        await registrar_evento(
            db,
            tenant,
            evento="ciclo_renovado",
            resumen=f"Ciclo renovado; {len(encolados)} tickets desbloqueados",
            actor_tipo="sistema",
        )
        owners = await todos(
            db,
            """SELECT id FROM core.users
                                     WHERE tenant_id=:t AND rol='owner'
                                       AND estado='activo'""",
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

    # FIX BE-F18: `encolados` ya está construida (o vacía si el bloque
    # abortó). El xadd se hace fuera del bloque para no bloquear la
    # transacción del tenant con I/O de Redis.
    r = redis_cliente()
    for tk in encolados:
        await r.xadd("kc:triage", {"ticket_id": tk["numero"], "tenant_id": tenant})
    log.info("billing.renovado", tenant_id=tenant, desbloqueados=len(encolados))
