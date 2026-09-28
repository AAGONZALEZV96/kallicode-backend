"""Espacio de nombres por tenant para las claves de redis"""

from __future__ import annotations


def clave_tenant(tenant_id: str, *partes: str) -> str:
    """Todas las claves de Redis deben estar en el espacio de nombres de
    cada tenant, para evitar colisiones entre tenants."""
    return "kc:t:" + tenant_id + ":" + ":".join(partes)
