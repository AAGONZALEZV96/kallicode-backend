"""Runner de workers de Kallicode.

Consume los streams de Redis (consumer groups) y ejecuta los jobs
programados. Un solo proceso corre todos los workers (D02: el volumen por
línea es modesto); escalar = más réplicas del contenedor (los consumer
groups reparten).

Streams:
    kc:inbox    webhooks crudos -> normalizer
    kc:triage   tickets nuevos  -> pipeline de triage (dedup + embeddings)
    kc:correo   correos salientes -> notifier
    kc:exports  exportaciones de auditoría -> audit_export

Programados (loop con intervalo):
    scheduler      cada 5 s  asigna jobs pendientes a líneas libres (QU-2)
    billing_cycle  cada hora renueva ciclos vencidos (D13)
    housekeeping   cada hora purgas (tokens, idempotencia, huérfanos)
"""

from __future__ import annotations

import asyncio
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

from . import audit_export, billing_cycle, housekeeping, normalizer, notifier, scheduler

_GRUPO = "kc-workers"
_STREAMS = {
    "kc:inbox": normalizer.procesar_inbox,
    "kc:triage": normalizer.procesar_triage,
    "kc:correo": notifier.procesar,
    "kc:exports": audit_export.procesar,
}
_PROGRAMADOS = [
    (scheduler.ciclo, 5),
    (billing_cycle.ciclo, 3600),
    (housekeeping.ciclo, 3600),
]
_parar = asyncio.Event()

# Variables dinámicas para el consumidor y configuración de retries
_CONSUMER_NAME = f"{socket.gethostname()}-{os.getpid()}"
_MAX_RETRIES = 3


async def procesar_mensaje(r, stream: str, mid, campos, handler) -> None:
    """Procesa un mensaje individual con reintentos y lógica de Dead Letter Queue (DLQ)."""
    limpiar_contexto()
    bind_contexto(trace_id=f"wk_{mid}")
    try:
        await handler(campos)
        await r.xack(stream, _GRUPO, mid)
    except Exception as exc:
        log.error(
            "workers.mensaje_fallido",
            stream=stream,
            mensaje_id=mid,
            exc_info=True,
        )
        mid_str = mid.decode("utf-8") if isinstance(mid, bytes) else str(mid)
        retry_key = f"kc:retries:{stream}:{mid_str}"
        attempts = await r.incr(retry_key)

        if attempts == 1:
            await r.expire(retry_key, 86400)

        if attempts >= _MAX_RETRIES:
            dlq_payload = {
                "original_stream": stream,
                "original_mid": mid_str,
                "payload": str(campos),
                "attempts": str(attempts),
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            # Evita OOM con MAXLEN aproximado al mandar a la DLQ
            await r.xadd("kc:dlq", dlq_payload, maxlen=5000, approximate=True)
            await r.xack(stream, _GRUPO, mid)
            await r.delete(retry_key)


async def _consumir(stream: str, handler) -> None:
    r = redis_cliente()
    try:
        await r.xgroup_create(stream, _GRUPO, id="0", mkstream=True)
    except Exception as exc:
        log.debug("workers.xgroup_create_ignorado", stream=stream, detalle=str(exc))

    while not _parar.is_set():
        try:
            lotes = await r.xreadgroup(_GRUPO, _CONSUMER_NAME, {stream: ">"}, count=10, block=2000)
            for _, mensajes in lotes or []:
                for mid, campos in mensajes:
                    await procesar_mensaje(r, stream, mid, campos, handler)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("workers.stream_error", stream=stream, exc_info=True)
            await asyncio.sleep(5)


async def _rescatar_huerfanos(stream: str, handler) -> None:
    r = redis_cliente()
    while not _parar.is_set():
        try:
            # Reclama mensajes de réplicas caídas que lleven >60s en la PEL
            res = await r.xautoclaim(
                name=stream,
                groupname=_GRUPO,
                consumername=_CONSUMER_NAME,
                minidletime=60000,
                start_id="0-0",
                count=10,
            )
            claimed_messages = res[1] if isinstance(res, tuple) and len(res) > 1 else []
            for mid, campos in claimed_messages:
                log.info("workers.rescatando_mensaje", stream=stream, mensaje_id=mid)
                await procesar_mensaje(r, stream, mid, campos, handler)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("workers.autoclaim_error", stream=stream, exc_info=True)

        try:
            await asyncio.wait_for(_parar.wait(), timeout=60.0)
        except TimeoutError:
            pass


async def _programado(fn, intervalo_s: int) -> None:
    while not _parar.is_set():
        limpiar_contexto()
        bind_contexto(trace_id=f"cron_{fn.__module__.split('.')[-1]}")
        try:
            await fn()
        except Exception:
            log.error("workers.cron_error", worker=fn.__module__, exc_info=True)
        try:
            await asyncio.wait_for(_parar.wait(), timeout=intervalo_s)
        except TimeoutError:
            pass


async def main() -> None:
    configurar_logging()
    log.info("workers.arrancando", streams=list(_STREAMS), programados=len(_PROGRAMADOS))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _parar.set)
        except NotImplementedError:
            pass

    # Arrancamos los consumidores normales, los rescatistas y los cron
    tareas = [asyncio.create_task(_consumir(s, h)) for s, h in _STREAMS.items()]
    tareas += [asyncio.create_task(_rescatar_huerfanos(s, h)) for s, h in _STREAMS.items()]
    tareas += [asyncio.create_task(_programado(fn, i)) for fn, i in _PROGRAMADOS]

    await _parar.wait()
    for t in tareas:
        t.cancel()
    log.info("workers.detenidos")


if __name__ == "__main__":
    asyncio.run(main())
