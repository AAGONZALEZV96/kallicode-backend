"""Consumidor de kc:realimentacion: realimentación del grafo con los tickets entregados.

KC-CM-10-4. La API encola {tenant_id, ticket_id} cuando un deploy a producción termina con éxito;
aquí se delega en CodeMapping (`kallicode_codemapping.realimentacion.procesar_mensaje`), que lee
el expediente, propone los candidatos y los deja pendientes de curación. Es idempotente por
ticket: un mensaje repetido (entrega al menos una vez) no duplica candidatos ni coste.

CodeMapping usa psycopg síncrono: el trabajo corre en un hilo para no bloquear el bucle.
Configuración: KC_CM_REALIMENTACION_* (.env.example).
"""

from __future__ import annotations

import asyncio
from functools import partial

from kallicode_core.config import get_settings
from kallicode_core.logging import log


async def procesar(campos: dict) -> None:
    s = get_settings()
    if not s.cm_realimentacion_habilitada:
        log.info("realimentacion.deshabilitada", ticket_id=campos.get("ticket_id"))
        return

    from kallicode_codemapping.realimentacion import procesar_mensaje

    resultado = await asyncio.to_thread(
        partial(
            procesar_mensaje,
            campos,
            s.database_url,
            presupuesto_tokens_ticket=s.cm_realimentacion_tokens_ticket,
            presupuesto_tokens_tenant_mes=s.cm_realimentacion_tokens_tenant_mes,
        )
    )
    if resultado is None:
        return
    log.info(
        "realimentacion.procesada",
        ticket_id=resultado.ticket_id,
        estado=resultado.estado,
        omitido=resultado.omitido,
        nuggets=resultado.nuggets_propuestos,
        incidente=resultado.incidente_propuesto,
        tokens_in=resultado.tokens_in,
        tokens_out=resultado.tokens_out,
    )
