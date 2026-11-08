"""Runner de workers de Kallicode.

Streams:
    kc:inbox     webhooks crudos
    kc:correo    correos salientes
    kc:exports   exportaciones de auditoría
    kc:adjuntos  confirmación y análisis de adjuntos

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

from kallicode_core.comercial import redis_cliente
from kallicode_core.logging import (
    bind_contexto,
    configurar_logging,
    limpiar_contexto,
    log,
)

from . import (
    adjuntos,
    audit_export,
    billing_cycle,
    housekeeping,
    normalizer,
    notifier,
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
}

_PROGRAMADOS = [
    ("scheduler", scheduler.ciclo, 5),
    ("billing_cycle", billing_cycle.ciclo, 3600),
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

            await redis.incr(f"kc:metrics:dlq:{stream}")
            await redis.xadd(
                "kc:dlq",
                dlq_payload,
                maxlen=5000,
                approximate=True,
            )
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

    tareas.append(asyncio.create_task(orquestador_ciclo(_parar)))

    await _parar.wait()

    for tarea in tareas:
        tarea.cancel()

    log.info("workers.detenidos")


if __name__ == "__main__":
    asyncio.run(main())
