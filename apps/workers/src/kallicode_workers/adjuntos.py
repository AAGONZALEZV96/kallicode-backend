"""Procesamiento y reintentos de confirmación de adjuntos."""

from kallicode_core.adjuntos import confirmar
from kallicode_core.db import sesion_tenant
from kallicode_core.logging import log


async def procesar(campos: dict) -> None:
    tenant_id = campos["tenant_id"]
    adjunto_id = campos["adjunto_id"]

    async with sesion_tenant(
        tenant_id,
        actor="worker:adjuntos",
    ) as db:
        resultado, error = await confirmar(
            db,
            tenant_id,
            adjunto_id,
        )

    # Lanzar después del commit conserva el consumo real.
    if error and error.http >= 500:
        raise error

    if error:
        log.warning(
            "adjuntos.rechazado",
            adjunto_id=adjunto_id,
            codigo=error.codigo,
        )
        return

    log.info("adjuntos.confirmado", **resultado)
