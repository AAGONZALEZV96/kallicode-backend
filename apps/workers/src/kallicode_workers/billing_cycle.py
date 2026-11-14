"""Renovación mensual del plan por aniversario (D13).

Corre cada hora; para las suscripciones activas con renueva_el <= hoy invoca
core.renovar_ciclo(tenant), que en una sola transacción (KC-DB-09-1, DB-F08):
  1. avanza renueva_el al próximo aniversario de inicia_el (meses cortos
     normalizados, sin arrastrar el día),
  2. abre el ciclo nuevo en usage_cycles (umbral normal) sin tocar el historial,
  3. desbloquea tickets en_cola_por_cuota y jobs pausado_por_cuota por
     prioridad y antigüedad, sin superar las líneas del plan,
  4. audita ciclo_renovado.
El worker añade lo que no es de base de datos: notificar a los owners y
encolar en kc:triage los tickets desbloqueados.

KC-BE-04-5 (cierra BE-F18): la idempotencia la garantiza la función (candado
de la suscripción + condición renueva_el <= hoy); `encolados` se inicializa
FUERA del `async with` para que un aborto del bloque no produzca NameError al
hacer el xadd.
"""

from __future__ import annotations

from kallicode_core.comercial import redis_cliente
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
             WHERE finaliza_el IS NULL AND deleted_at IS NULL
               AND estado IN ('activa', 'periodo_gracia')
               AND renueva_el <= current_date
            FOR UPDATE SKIP LOCKED
            """,
        )
    for fila in vencidas:
        await _renovar(fila["tenant_id"])


async def _renovar(tenant: str) -> None:
    # FIX BE-F18: inicializar FUERA del bloque para que un aborto no deje
    # `encolados` sin definir al hacer el xadd.
    encolados: list[str] = []

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

        encolados = list(resultado["tickets_desbloqueados"] or [])

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

    # El xadd va fuera del bloque para no retener la transacción del tenant con
    # I/O de Redis; solo se encolan los tickets que la función desbloqueó.
    r = redis_cliente()
    for numero in encolados:
        await r.xadd("kc:triage", {"ticket_id": numero, "tenant_id": tenant})
    log.info(
        "billing.renovado",
        tenant_id=tenant,
        ciclo=resultado["ciclo_nuevo"],
        desbloqueados=len(encolados),
        jobs=resultado["jobs_desbloqueados"],
    )
