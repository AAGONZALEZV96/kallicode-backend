"""Runner de workers de Kallicode.

Consume los streams de Redis (consumer groups) y ejecuta los jobs
programados. Un solo proceso corre todos los workers (D02: el volumen por
línea es modesto); escalar = más réplicas del contenedor (los consumer
groups reparten).

Streams:
    kc:inbox    webhooks crudos -> normalizer
    kc:correo   correos salientes -> notifier
    kc:exports  exportaciones de auditoría -> audit_export

NOTA (KC-BE-04-2): el stream `kc:triage` se ha retirado. El triage (LLM)
lo ejecuta el orquestador sobre `core.jobs` directamente, no un worker
que escuche un stream. El normalizador ya no invoca al LLM.

Programados (loop con intervalo, con leader election KC-BE-04-5):
    scheduler      cada 5 s  asigna jobs pendientes a líneas libres (QU-2)
    billing_cycle  cada hora renueva ciclos vencidos (D13)
    housekeeping   cada hora purgas (tokens, idempotencia, huérfanos)
    orquestador    bucle continuo: reserva job → ejecuta etapa → transiciona

Cada programado toma `pg_try_advisory_lock(nombre)` antes de ejecutarse.
Con N réplicas, solo una ejecuta cada ciclo; las demás no hacen nada y
esperan al siguiente intervalo. Si el proceso muere, PostgreSQL libera
el lock solo.
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

from . import audit_export, billing_cycle, housekeeping, normalizer, notifier, scheduler
from .lider import con_lider
from .orquestador.bucle import ciclo as orquestador_ciclo

_GRUPO = "kc-workers"
_STREAMS = {
    "kc:inbox": normalizer.procesar_inbox,
    # "kc:triage": normalizer.procesar_triage,   # KC-BE-04-2: retirado
    "kc:correo": notifier.procesar,
    "kc:exports": audit_export.procesar,
}
# KC-BE-04-5: cada programado lleva su nombre (clave del advisory lock).
_PROGRAMADOS = [
    ("scheduler", scheduler.ciclo, 5),
    ("billing_cycle", billing_cycle.ciclo, 3600),
    ("housekeeping", housekeeping.ciclo, 3600),
]
_parar = asyncio.Event()

# Variables dinámicas para el consumidor y configuración de retries
_CONSUMER_NAME = f"{socket.gethostname()}-{os.getpid()}"
_MAX_RETRIES = 3


async def procesar_mensaje(r, stream: str, mid, campos, handler) -> None:
    """Procesa un mensaje individual con reintentos y lógica de DLQ."""
    limpiar_contexto()

    campos_str = (
        {
            (k.decode("utf-8") if isinstance(k, bytes) else k): (
                v.decode("utf-8") if isinstance(v, bytes) else v
            )
            for k, v in campos.items()
        }
        if isinstance(campos, dict)
        else {}
    )

    tenant_id = campos_str.get("tenant_id")
    actor = campos_str.get("actor")

    kwargs_contexto = {"trace_id": f"wk_{mid}"}
    if tenant_id:
        kwargs_contexto["tenant_id"] = tenant_id
    if actor:
        kwargs_contexto["actor"] = actor

    bind_contexto(**kwargs_contexto)

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
                # JSON (no repr de Python): el endpoint de reproceso lo
                # deserializa con json.loads y lo reinyecta al stream de
                # origen sin pérdida.
                "payload": json.dumps(campos_str),
                "attempts": str(attempts),
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
            await r.incr(f"kc:metrics:dlq:{stream}")
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


async def _programado(nombre: str, fn, intervalo_s: int) -> None:
    """Ejecuta `fn` cada `intervalo_s`, solo si esta réplica es líder.

    KC-BE-04-5: `con_lider` toma `pg_try_advisory_lock(nombre)` y lo
    libera al salir del bloque. Con N réplicas, solo una ejecuta cada
    ciclo; las demás no hacen nada. Si el proceso muere, PostgreSQL
    libera el lock solo.
    """
    while not _parar.is_set():
        limpiar_contexto()
        bind_contexto(trace_id=f"cron_{nombre}")
        try:
            async with con_lider(nombre) as es_lider:
                if es_lider:
                    await fn()
        except Exception:
            log.error(
                "workers.cron_error",
                worker=fn.__module__,
                programado=nombre,
                exc_info=True,
            )
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

    tareas = [asyncio.create_task(_consumir(s, h)) for s, h in _STREAMS.items()]
    tareas += [asyncio.create_task(_rescatar_huerfanos(s, h)) for s, h in _STREAMS.items()]
    # KC-BE-04-5: (nombre, fn, intervalo) → _programado(nombre, fn, intervalo)
    tareas += [asyncio.create_task(_programado(nombre, fn, i)) for nombre, fn, i in _PROGRAMADOS]
    # KC-BE-04-2: el orquestador corre como bucle continuo (no programado).
    tareas += [asyncio.create_task(orquestador_ciclo(_parar))]

    await _parar.wait()
    for t in tareas:
        t.cancel()
    log.info("workers.detenidos")


if __name__ == "__main__":
    asyncio.run(main())
