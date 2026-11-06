"""Runner de workers de Kallicode.

Streams consumidos:

    kc:inbox
        Webhooks pendientes de normalización.

    kc:correo
        Correos salientes.

    kc:exports
        Exportaciones de auditoría.

    kc:attachments
        Adjuntos confirmados pendientes de análisis antimalware.

Los streams utilizan consumer groups, reintentos y DLQ. Los trabajos
programados utilizan advisory locks para que una sola réplica ejecute
cada ciclo.
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
    "kc:attachments": adjuntos.procesar,
}

_PROGRAMADOS = [
    (
        "scheduler",
        scheduler.ciclo,
        5,
    ),
    (
        "billing_cycle",
        billing_cycle.ciclo,
        3600,
    ),
    (
        "housekeeping",
        housekeeping.ciclo,
        3600,
    ),
]

_parar = asyncio.Event()

_CONSUMER_NAME = f"{socket.gethostname()}-{os.getpid()}"

_MAX_RETRIES = 3


def _normalizar_campos(
    campos: dict,
) -> dict[str, str]:
    """Convierte claves y valores bytes de Redis a strings."""
    if not isinstance(campos, dict):
        return {}

    return {
        (clave.decode("utf-8") if isinstance(clave, bytes) else str(clave)): (
            valor.decode("utf-8") if isinstance(valor, bytes) else str(valor)
        )
        for clave, valor in campos.items()
    }


async def procesar_mensaje(
    redis,
    stream: str,
    mensaje_id,
    campos,
    handler,
) -> None:
    """Procesa un mensaje individual con reintentos y DLQ."""
    limpiar_contexto()

    campos_str = _normalizar_campos(campos)

    tenant_id = campos_str.get("tenant_id")
    actor = campos_str.get("actor")

    mensaje_id_str = (
        mensaje_id.decode("utf-8") if isinstance(mensaje_id, bytes) else str(mensaje_id)
    )

    contexto = {
        "trace_id": f"wk_{mensaje_id_str}",
    }

    if tenant_id:
        contexto["tenant_id"] = tenant_id

    if actor:
        contexto["actor"] = actor

    bind_contexto(**contexto)

    try:
        await handler(campos_str)

        await redis.xack(
            stream,
            _GRUPO,
            mensaje_id,
        )

        await redis.delete(f"kc:retries:{stream}:{mensaje_id_str}")
    except Exception as exc:
        log.error(
            "workers.mensaje_fallido",
            stream=stream,
            mensaje_id=mensaje_id_str,
            exc_info=True,
        )

        retry_key = f"kc:retries:{stream}:{mensaje_id_str}"

        intentos = await redis.incr(retry_key)

        if intentos == 1:
            await redis.expire(
                retry_key,
                86400,
            )

        if intentos < _MAX_RETRIES:
            return

        dlq_payload = {
            "original_stream": stream,
            "original_mid": mensaje_id_str,
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

        await redis.xack(
            stream,
            _GRUPO,
            mensaje_id,
        )

        await redis.delete(retry_key)


async def _crear_grupo(
    redis,
    stream: str,
) -> None:
    """Crea el consumer group si todavía no existe."""
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


async def _consumir(
    stream: str,
    handler,
) -> None:
    """Consume mensajes nuevos de un stream."""
    redis = redis_cliente()

    await _crear_grupo(
        redis,
        stream,
    )

    while not _parar.is_set():
        try:
            lotes = await redis.xreadgroup(
                _GRUPO,
                _CONSUMER_NAME,
                {
                    stream: ">",
                },
                count=10,
                block=2000,
            )

            for _, mensajes in lotes or []:
                for mensaje_id, campos in mensajes:
                    await procesar_mensaje(
                        redis,
                        stream,
                        mensaje_id,
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


async def _rescatar_huerfanos(
    stream: str,
    handler,
) -> None:
    """Recupera mensajes cuyo consumidor desapareció."""
    redis = redis_cliente()

    while not _parar.is_set():
        try:
            resultado = await redis.xautoclaim(
                name=stream,
                groupname=_GRUPO,
                consumername=_CONSUMER_NAME,
                min_idle_time=60_000,
                start_id="0-0",
                count=10,
            )

            mensajes = (
                resultado[1] if isinstance(resultado, (list, tuple)) and len(resultado) > 1 else []
            )

            for mensaje_id, campos in mensajes:
                log.info(
                    "workers.rescatando_mensaje",
                    stream=stream,
                    mensaje_id=mensaje_id,
                )

                await procesar_mensaje(
                    redis,
                    stream,
                    mensaje_id,
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
    """Ejecuta una tarea periódica bajo advisory lock."""
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
    """Inicia consumidores, tareas periódicas y orquestador."""
    configurar_logging()

    log.info(
        "workers.arrancando",
        streams=list(_STREAMS),
        programados=len(_PROGRAMADOS),
    )

    loop = asyncio.get_running_loop()

    for sig in (
        signal.SIGINT,
        signal.SIGTERM,
    ):
        try:
            loop.add_signal_handler(
                sig,
                _parar.set,
            )
        except NotImplementedError:
            pass

    tareas = [
        asyncio.create_task(
            _consumir(
                stream,
                handler,
            )
        )
        for stream, handler in _STREAMS.items()
    ]

    tareas += [
        asyncio.create_task(
            _rescatar_huerfanos(
                stream,
                handler,
            )
        )
        for stream, handler in _STREAMS.items()
    ]

    tareas += [
        asyncio.create_task(
            _programado(
                nombre,
                funcion,
                intervalo,
            )
        )
        for nombre, funcion, intervalo in _PROGRAMADOS
    ]

    tareas.append(asyncio.create_task(orquestador_ciclo(_parar)))

    await _parar.wait()

    for tarea in tareas:
        tarea.cancel()

    await asyncio.gather(
        *tareas,
        return_exceptions=True,
    )

    log.info("workers.detenidos")


if __name__ == "__main__":
    asyncio.run(main())
