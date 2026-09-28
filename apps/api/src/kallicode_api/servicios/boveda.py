"""Bóveda de secretos.

Local: los secretos viven en Redis con prefijo kc:boveda: (suficiente para
desarrollo; NO producción). TODO(produccion): Azure Key Vault vía
azure-keyvault-secrets con managed identity — misma interfaz.
"""

from __future__ import annotations

import json

from kallicode_core.comercial import redis_cliente
from kallicode_core.logging import log
from kallicode_core.redis_keys import clave_tenant


async def guardar(tenant_id: str, nombre: str, valor: dict) -> str:
    """Guarda el secreto y devuelve su referencia (lo que persiste la BD)."""
    await redis_cliente().set(clave_tenant(tenant_id, "boveda", nombre), json.dumps(valor))
    log.info("boveda.guardado", ref=nombre)  # nunca el valor
    return nombre


async def leer(tenant_id: str, nombre: str) -> dict | None:
    crudo = await redis_cliente().get(clave_tenant(tenant_id, "boveda", nombre))
    return json.loads(crudo) if crudo else None


async def eliminar(tenant_id: str, nombre: str) -> None:
    await redis_cliente().delete(clave_tenant(tenant_id, "boveda", nombre))
    log.info("boveda.eliminado", ref=nombre)
