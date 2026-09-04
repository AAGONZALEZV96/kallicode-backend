"""Purgas programadas (§9.3 del doc de BD): tokens expirados, idempotencia
>48 h, adjuntos huérfanos >48 h, inbox procesado >90 días. Además ejecuta
core.validar_integridad() y alerta si algún chequeo devuelve problemas."""
from __future__ import annotations

from sqlalchemy import text

from kallicode_core.db import sesion_sistema, todos
from kallicode_core.logging import log


async def ciclo() -> None:
    async with sesion_sistema() as db:
        r1 = await db.execute(text(
            "DELETE FROM core.refresh_tokens WHERE expira_en < now() - interval '7 days'"))
        r2 = await db.execute(text(
            "DELETE FROM core.idempotency_keys WHERE creado_en < now() - interval '48 hours'"))
        r3 = await db.execute(text("""
            DELETE FROM core.ticket_attachments
             WHERE ticket_id IS NULL AND creado_en < now() - interval '48 hours'"""))
        r4 = await db.execute(text("""
            DELETE FROM core.webhook_inbox
             WHERE estado = 'procesado' AND procesado_en < now() - interval '90 days'"""))
        problemas = await todos(db,
            "SELECT chequeo, problemas FROM core.validar_integridad() WHERE problemas > 0")
    log.info("housekeeping.purgas", tokens=r1.rowcount, idempotencia=r2.rowcount,
             adjuntos=r3.rowcount, inbox=r4.rowcount)
    for p in problemas:
        log.error("housekeeping.integridad", chequeo=p["chequeo"],
                  problemas=p["problemas"])
