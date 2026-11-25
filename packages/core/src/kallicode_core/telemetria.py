"""Transporte de telemetría compartido por la API y los workers.

Redis reúne contadores de procesos distintos. PostgreSQL conserva el consumo
facturable; ningún contador de Redis es fuente de facturación.
"""

from __future__ import annotations

import asyncio
from bisect import bisect_left
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import time

from . import comercial
from .logging import log

CLAVE_HTTP = "kc:metrics:http"
CLAVE_ESCALADOS = "kc:metrics:escalados"
CLAVE_ORQUESTADOR = "kc:metrics:orquestador"
CLAVE_AUDITORIA = "kc:metrics:auditoria"
PREFIJO_DLQ = "kc:metrics:dlq:"
STREAM_DLQ = "kc:dlq"
STREAMS = ("kc:inbox", "kc:correo", "kc:exports", "kc:adjuntos", "kc:realimentacion")
BUCKETS_HTTP = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
ORQUESTADOR_TTL_S = 20


@asynccontextmanager
async def escritura_metricas(operacion: str) -> AsyncIterator[None]:
    """La telemetría de Redis tiene un presupuesto corto y nunca falla la operación de negocio."""
    try:
        async with asyncio.timeout(0.1):
            yield
    except Exception as exc:
        log.debug("metricas.escritura_fallida", operacion=operacion, error=type(exc).__name__)


async def registrar_http(
    superficie: str, ruta: str, metodo: str, segundos: float, error: str | None
) -> None:
    # Persiste el bucket individual. Los buckets acumulados se calculan al exponerlos.
    clave = f"{superficie}|{metodo}|{ruta}"
    bucket = bisect_left(BUCKETS_HTTP, segundos)
    async with escritura_metricas("http"), comercial.redis_cliente().pipeline() as pipe:
        pipe.hincrby(CLAVE_HTTP, f"{clave}|{bucket}", 1)
        pipe.hincrbyfloat(CLAVE_HTTP, f"{clave}|suma", segundos)
        if error:
            pipe.hincrby(CLAVE_HTTP, f"error|{superficie}|{error}", 1)
        await pipe.execute()


async def registrar_escalado(tier_origen: str, tier_destino: str) -> None:
    async with escritura_metricas("escalado"):
        await comercial.redis_cliente().hincrby(CLAVE_ESCALADOS, f"{tier_origen}|{tier_destino}", 1)


async def pulso_orquestador(instancia: str) -> None:
    ahora = time()
    async with (
        escritura_metricas("pulso_orquestador"),
        comercial.redis_cliente().pipeline() as pipe,
    ):
        pipe.zadd(CLAVE_ORQUESTADOR, {instancia: ahora})
        pipe.zremrangebyscore(CLAVE_ORQUESTADOR, "-inf", ahora - ORQUESTADOR_TTL_S)
        await pipe.execute()
