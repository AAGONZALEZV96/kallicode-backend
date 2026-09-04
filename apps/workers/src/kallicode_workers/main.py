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
    scheduler       cada 5 s   asigna jobs pendientes a líneas libres (QU-2)
    billing_cycle   cada hora  renueva ciclos vencidos (D13)
    housekeeping    cada hora  purgas (tokens, idempotencia, huérfanos)
"""
from __future__ import annotations

import asyncio
import signal

from kallicode_core.comercial import redis_cliente
from kallicode_core.logging import bind_contexto, configurar_logging, limpiar_contexto, log

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


async def _consumir(stream: str, handler) -> None:
    r = redis_cliente()
    try:
        await r.xgroup_create(stream, _GRUPO, id="0", mkstream=True)
    except Exception:
        pass  # el grupo ya existe
    consumidor = f"w-{stream.split(':')[1]}"
    while not _parar.is_set():
        try:
            lotes = await r.xreadgroup(_GRUPO, consumidor, {stream: ">"},
                                       count=10, block=2000)
            for _, mensajes in lotes or []:
                for mid, campos in mensajes:
                    limpiar_contexto()
                    bind_contexto(trace_id=f"wk_{mid}")
                    try:
                        await handler(campos)
                        await r.xack(stream, _GRUPO, mid)
                    except Exception:
                        log.error("workers.mensaje_fallido", stream=stream,
                                  mensaje_id=mid, exc_info=True)
                        await r.xack(stream, _GRUPO, mid)  # inbox conserva el estado
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("workers.stream_error", stream=stream, exc_info=True)
            await asyncio.sleep(5)


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
        except asyncio.TimeoutError:
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
    tareas = [asyncio.create_task(_consumir(s, h)) for s, h in _STREAMS.items()]
    tareas += [asyncio.create_task(_programado(fn, i)) for fn, i in _PROGRAMADOS]
    await _parar.wait()
    for t in tareas:
        t.cancel()
    log.info("workers.detenidos")


if __name__ == "__main__":
    asyncio.run(main())
