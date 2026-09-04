"""Bóveda de secretos.

Local: los secretos viven en Redis con prefijo kc:boveda: (suficiente para
desarrollo; NO producción). TODO(produccion): Azure Key Vault vía
azure-keyvault-secrets con managed identity — misma interfaz.
"""
from __future__ import annotations

import json

from kallicode_core.comercial import redis_cliente
from kallicode_core.logging import log


async def guardar(nombre: str, valor: dict) -> str:
    """Guarda el secreto y devuelve su referencia (lo que persiste la BD)."""
    await redis_cliente().set(f"kc:boveda:{nombre}", json.dumps(valor))
    log.info("boveda.guardado", ref=nombre)  # nunca el valor
    return nombre


async def leer(nombre: str) -> dict | None:
    crudo = await redis_cliente().get(f"kc:boveda:{nombre}")
    return json.loads(crudo) if crudo else None


async def eliminar(nombre: str) -> None:
    await redis_cliente().delete(f"kc:boveda:{nombre}")
    log.info("boveda.eliminado", ref=nombre)
