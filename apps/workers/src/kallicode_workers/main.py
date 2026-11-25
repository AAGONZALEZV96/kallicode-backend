"""Runner de workers de Kallicode.

Streams:
    kc:inbox     webhooks crudos
    kc:correo    correos salientes
    kc:exports   exportaciones de auditoría
    kc:adjuntos  confirmación y análisis de adjuntos
    kc:realimentacion  tickets entregados -> candidatos de curación (KC-CM-10-4)

El orquestador consume core.jobs mediante su propio bucle.
Los trabajos programados utilizan elección de líder.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import traceback
from time import time

from kallicode_core.comercial import redis_cliente
from kallicode_core.db import sesion_sistema, valor
from kallicode_core.logging import (
    bind_contexto,
    configurar_logging,
    limpiar_contexto,
    log,
)
from kallicode_core.telemetria import (
    CLAVE_AUDITORIA,
    CLAVE_ORQUESTADOR,
    PREFIJO_DLQ,
    STREAM_DLQ,
    escritura_metricas,
    pulso_orquestador,
)
from sqlalchemy import text

from . import (
    adjuntos,
    audit_export,
    avisos_cuota,
    billing_cycle,
    housekeeping,
    normalizer,
    notifier,
    realimentacion,
    salud,
    scheduler,
)
from .lider import con_lider
from .orquestador.bucle import ciclo as orquestador_ciclo

_GRUPO = "kc-workers"

_STREAMS = {
    "kc:inbox": normalizer.procesar_inbox,
    "kc:correo": notifier.procesar,
    "kc:exports": audit_export.procesar,
    "kc:adjuntos": adjuntos.procesar,
    "kc:realimentacion": realimentacion.procesar,
}

_PROGRAMADOS = [
    ("scheduler", scheduler.ciclo, 5),
    ("billing_cycle", billing_cycle.ciclo, 3600),
    ("avisos_comerciales", notifier.avisar_comercial, 60),
    ("avisos_cuota", avisos_cuota.ciclo, 3600),
    ("housekeeping", housekeeping.ciclo, 3600),
]

_parar = asyncio.Event()
_CONSUMER_NAME = f"{socket.gethostname()}-{os.getpid()}"
_MAX_RETRIES = 3


async def procesar_mensaje(
    redis,
    stream: str,
    mid,
    campos,
    handler,
) -> None:
    """Procesa un mensaje con reintentos y DLQ."""
    limpiar_contexto()

    campos_str = (
        {
            (clave.decode("utf-8") if isinstance(clave, bytes) else clave): (
                valor.decode("utf-8") if isinstance(valor, bytes) else valor
            )
            for clave, valor in campos.items()
        }
        if isinstance(campos, dict)
        else {}
    )

    contexto = {"trace_id": f"wk_{mid}"}

    if campos_str.get("tenant_id"):
        contexto["tenant_id"] = campos_str["tenant_id"]

    if campos_str.get("actor"):
        contexto["actor"] = campos_str["actor"]

    bind_contexto(**contexto)

    try:
        await handler(campos_str)
        await redis.xack(stream, _GRUPO, mid)

    except Exception as exc:
        log.error(
            "workers.mensaje_fallido",
            stream=stream,
            mensaje_id=mid,
            exc_info=True,
        )

        mid_str = mid.decode("utf-8") if isinstance(mid, bytes) else str(mid)
        retry_key = f"kc:retries:{stream}:{mid_str}"
        intentos = await redis.incr(retry_key)

        if intentos == 1:
            await redis.expire(retry_key, 86400)

        if intentos >= _MAX_RETRIES:
            dlq_payload = {
                "original_stream": stream,
                "original_mid": mid_str,
                "payload": json.dumps(campos_str),
                "attempts": str(intentos),
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }

            await redis.xadd(
                STREAM_DLQ,
                dlq_payload,
                maxlen=5000,
                approximate=True,
            )
            # Cuenta entradas reales a la DLQ; un fallo del contador no bloquea el ACK.
            async with escritura_metricas("dlq"):
                await redis.incr(f"{PREFIJO_DLQ}{stream}")
            await redis.xack(stream, _GRUPO, mid)
            await redis.delete(retry_key)


async def _consumir(stream: str, handler) -> None:
    redis = redis_cliente()

    try:
        await redis.xgroup_create(
            stream,
            _GRUPO,
            id="0",
            mkstream=True,
        )
    except Exception as exc:
        log.debug(
            "workers.xgroup_create_ignorado",
            stream=stream,
            detalle=str(exc),
        )

    while not _parar.is_set():
        try:
            lotes = await redis.xreadgroup(
                _GRUPO,
                _CONSUMER_NAME,
                {stream: ">"},
                count=10,
                block=2000,
            )

            for _, mensajes in lotes or []:
                for mid, campos in mensajes:
                    await procesar_mensaje(
                        redis,
                        stream,
                        mid,
                        campos,
                        handler,
                    )

        except asyncio.CancelledError:
            raise

        except Exception:
            log.error(
                "workers.stream_error",
                stream=stream,
                exc_info=True,
            )
            await asyncio.sleep(5)


async def _rescatar_huerfanos(stream: str, handler) -> None:
    redis = redis_cliente()

    while not _parar.is_set():
        try:
            resultado = await redis.xautoclaim(
                name=stream,
                groupname=_GRUPO,
                consumername=_CONSUMER_NAME,
                min_idle_time=60000,
                start_id="0-0",
                count=10,
            )

            mensajes = (
                resultado[1] if isinstance(resultado, (tuple, list)) and len(resultado) > 1 else []
            )

            for mid, campos in mensajes:
                log.info(
                    "workers.rescatando_mensaje",
                    stream=stream,
                    mensaje_id=mid,
                )

                await procesar_mensaje(
                    redis,
                    stream,
                    mid,
                    campos,
                    handler,
                )

        except asyncio.CancelledError:
            raise

        except Exception:
            log.error(
                "workers.autoclaim_error",
                stream=stream,
                exc_info=True,
            )

        try:
            await asyncio.wait_for(
                _parar.wait(),
                timeout=60.0,
            )
        except TimeoutError:
            pass


async def _programado(
    nombre: str,
    funcion,
    intervalo_s: int,
) -> None:
    """Ejecuta el trabajo únicamente cuando esta réplica es líder."""
    while not _parar.is_set():
        limpiar_contexto()
        bind_contexto(trace_id=f"cron_{nombre}")

        try:
            async with con_lider(nombre) as es_lider:
                if es_lider:
                    await funcion()

        except Exception:
            log.error(
                "workers.cron_error",
                worker=funcion.__module__,
                programado=nombre,
                exc_info=True,
            )

        try:
            await asyncio.wait_for(
                _parar.wait(),
                timeout=intervalo_s,
            )
        except TimeoutError:
            pass


async def comprobar_cadenas_auditoria() -> None:
    """Una réplica comprueba toda la cadena, fuera del scrape y sin exponer eventos."""
    async with sesion_sistema() as db:
        await db.execute(text("SET LOCAL statement_timeout = '120s'"))
        rotas = await valor(
            db,
            """
            SELECT count(*) FROM (
                SELECT h.tenant_id FROM audit.chain_heads h
                CROSS JOIN LATERAL audit.verify_chain(h.tenant_id) v
                WHERE NOT v.integra OR (v.eventos_verificados > 0 AND h.ultimo_sello
                    IS DISTINCT FROM (SELECT e.sello FROM audit.audit_events e
                                      WHERE e.tenant_id = h.tenant_id ORDER BY e.id DESC LIMIT 1))
                UNION
                SELECT e.tenant_id FROM audit.audit_events e
                WHERE NOT EXISTS (SELECT 1 FROM audit.chain_heads h WHERE h.tenant_id = e.tenant_id)
            ) rotas
        """,
        )
    async with escritura_metricas("auditoria"):
        await redis_cliente().hset(
            CLAVE_AUDITORIA, mapping={"rotas": rotas, "verificada_en": time()}
        )


async def _publicar_pulso_orquestador(tarea: asyncio.Task) -> None:
    # El pulso vive mientras la tarea del orquestador sigue activa, incluso
    # durante una etapa larga. El TTL cubre caídas abruptas de la réplica.
    try:
        while not tarea.done():
            await pulso_orquestador(_CONSUMER_NAME)
            await asyncio.wait({tarea}, timeout=5)
    finally:
        async with escritura_metricas("baja_orquestador"):
            await redis_cliente().zrem(CLAVE_ORQUESTADOR, _CONSUMER_NAME)


async def main() -> None:
    configurar_logging()

    log.info(
        "workers.arrancando",
        streams=list(_STREAMS),
        programados=len(_PROGRAMADOS),
    )

    loop = asyncio.get_running_loop()

    for senal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(senal, _parar.set)
        except NotImplementedError:
            pass

    tareas = [
        asyncio.create_task(_consumir(stream, handler)) for stream, handler in _STREAMS.items()
    ]

    tareas += [
        asyncio.create_task(_rescatar_huerfanos(stream, handler))
        for stream, handler in _STREAMS.items()
    ]

    tareas += [
        asyncio.create_task(_programado(nombre, funcion, intervalo))
        for nombre, funcion, intervalo in _PROGRAMADOS
    ]

    orquestador = asyncio.create_task(orquestador_ciclo(_parar))
    salud.registrar_orquestador(orquestador)
    tareas.extend(
        (
            orquestador,
            asyncio.create_task(_publicar_pulso_orquestador(orquestador)),
            asyncio.create_task(salud.servir_salud(_parar)),
            asyncio.create_task(
                _programado("telemetria_auditoria", comprobar_cadenas_auditoria, 300)
            ),
        )
    )

    try:
        await _parar.wait()
    finally:
        for tarea in tareas:
            tarea.cancel()
        await asyncio.gather(*tareas, return_exceptions=True)

    log.info("workers.detenidos")


if __name__ == "__main__":
    asyncio.run(main())
