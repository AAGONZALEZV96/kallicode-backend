"""Purgas programadas (§9.3 del doc de BD): tokens expirados, idempotencia
>48 h, adjuntos huérfanos >48 h y las particiones vencidas de las colas (webhook_inbox,
notifications: DROP de la partición, no DELETE; KC-DB-07-3). Además ejecuta
core.validar_integridad() y alerta si algún chequeo devuelve problemas, y alerta si el dato más
antiguo de una tabla supera su retención declarada en más de 7 días (core.alertas_retencion,
KC-DB-10-4)."""

from __future__ import annotations

from kallicode_core.db import sesion_sistema, todos
from kallicode_core.logging import log
from sqlalchemy import text


async def ciclo() -> None:
    async with sesion_sistema() as db:
        r1 = await db.execute(
            text("DELETE FROM core.refresh_tokens WHERE expira_en < now() - interval '7 days'")
        )
        # KC-DB-10-3: el TTL (48 h) vive en la función SQL; pg_cron la ejecuta cada hora y esta llamada
        # es el respaldo si pg_cron no está disponible.
        r2 = await db.execute(text("SELECT core.purgar_idempotency_keys()"))
        r3 = await db.execute(
            text("""
            DELETE FROM core.ticket_attachments
             WHERE ticket_id IS NULL AND creado_en < now() - interval '48 hours'""")
        )
        # KC-DB-07-3: webhook_inbox y notifications están particionadas por mes; vencer un mes es
        # un DROP de la partición (instantáneo, sin bloat), no un DELETE masivo. La función no
        # elimina una partición con entregas pendientes y devuelve qué hizo con cada una.
        particiones = await todos(
            db,
            "SELECT tabla, particion, filas, accion, motivo FROM core.purgar_particiones_vencidas()",
        )
        problemas = await todos(
            db,
            "SELECT chequeo, problemas FROM core.validar_integridad() WHERE problemas > 0",
        )
        # KC-DB-10-4: se consulta después de purgar, así que una alerta es retención realmente incumplida.
        alertas = await todos(
            db,
            "SELECT tabla, tenant, exceso::text AS exceso FROM core.alertas_retencion()",
        )
    log.info(
        "housekeeping.purgas",
        tokens=r1.rowcount,
        idempotencia=r2.scalar_one(),
        adjuntos=r3.rowcount,
        particiones_eliminadas=sum(1 for p in particiones if p["accion"] == "eliminada"),
        filas_purgadas=sum(p["filas"] for p in particiones if p["accion"] == "eliminada"),
    )
    for p in particiones:
        if p["accion"] == "omitida":
            log.warning(
                "housekeeping.particion_omitida",
                tabla=p["tabla"],
                particion=p["particion"],
                motivo=p["motivo"],
            )
    for p in problemas:
        log.error("housekeeping.integridad", chequeo=p["chequeo"], problemas=p["problemas"])
    for a in alertas:
        log.error(
            "housekeeping.retencion_excedida",
            tabla=a["tabla"],
            tenant=a["tenant"],
            exceso=a["exceso"],
        )
