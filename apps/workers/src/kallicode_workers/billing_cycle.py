"""Renovación mensual del plan por aniversario (D13).

Corre cada hora; para las suscripciones con renueva_el <= hoy:
  1. avanza renueva_el un mes (normalizando meses cortos),
  2. crea el registro del nuevo ciclo en usage_cycles (umbral normal),
  3. desbloquea tickets en_cola_por_cuota y jobs pausado_por_cuota,
  4. audita ciclo_renovado y notifica al owner.
"""
from __future__ import annotations

from sqlalchemy import text

from kallicode_core.auditoria import registrar_evento
from kallicode_core.comercial import ciclo_actual, redis_cliente
from kallicode_core.db import sesion_sistema, sesion_tenant, todos
from kallicode_core.ids import nuevo
from kallicode_core.logging import log


async def ciclo() -> None:
    async with sesion_sistema() as db:
        vencidas = await todos(db, """
            SELECT tenant_id FROM core.subscriptions
             WHERE finaliza_el IS NULL AND renueva_el <= current_date""")
    for fila in vencidas:
        await _renovar(fila["tenant_id"])


async def _renovar(tenant: str) -> None:
    async with sesion_tenant(tenant, actor="svc:billing") as db:
        await db.execute(text("""
            UPDATE core.subscriptions
               SET renueva_el = (renueva_el + interval '1 month')::date
             WHERE tenant_id = :t AND finaliza_el IS NULL"""), {"t": tenant})
        await db.execute(text("""
            INSERT INTO core.usage_cycles (tenant_id, ciclo)
            VALUES (:t, :c) ON CONFLICT DO NOTHING"""),
            {"t": tenant, "c": ciclo_actual()})
        encolados = await todos(db, """
            UPDATE core.tickets SET etapa = 'triage'
             WHERE tenant_id = :t AND etapa = 'en_cola_por_cuota'
             RETURNING numero""", {"t": tenant})
        await db.execute(text("""
            UPDATE core.jobs SET estado = 'pendiente_asignacion'
             WHERE tenant_id = :t AND estado = 'pausado_por_cuota'"""), {"t": tenant})
        await registrar_evento(db, tenant, evento="ciclo_renovado",
                               resumen=f"Ciclo renovado; {len(encolados)} tickets "
                                       "desbloqueados", actor_tipo="sistema")
        owners = await todos(db, """SELECT id FROM core.users
                                     WHERE tenant_id=:t AND rol='owner'
                                       AND estado='activo'""", {"t": tenant})
        for o in owners:
            await db.execute(text("""
                INSERT INTO core.notifications (id, tenant_id, user_id, tipo, titulo,
                                                cuerpo)
                VALUES (:i, :t, :u, 'sistema', 'Ciclo renovado',
                        'Tu cuota mensual de LOC se ha renovado.')"""),
                {"i": nuevo("nt"), "t": tenant, "u": o["id"]})
    r = redis_cliente()
    for tk in encolados:
        await r.xadd("kc:triage", {"ticket_id": tk["numero"], "tenant_id": tenant})
    log.info("billing.renovado", tenant_id=tenant, desbloqueados=len(encolados))
