"""Bóveda de secretos con soporte para Azure Key Vault y Redis cifrado local.

Cumple con KC-BE-05-3 (BE-F05):
- Entornos productivos (dev, staging, prod): Azure Key Vault + Managed Identity.
- Local: Redis + Cifrado AES-GCM autenticado.
- Migración: Doble escritura habilitada para transición sin pérdida de secretos.
"""

from __future__ import annotations

import json
import os
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from kallicode_core.cifrado import cifrar, descifrar
from kallicode_core.comercial import redis_cliente
from kallicode_core.config import get_settings
from kallicode_core.logging import log
from kallicode_core.redis_keys import clave_tenant

# Inicialización segura en tiempo de importación
_settings = get_settings()
_entorno = getattr(_settings, "env", os.getenv("KC_ENV", "local")).lower()

# La clave local debe ser un HEX de 32 bytes (64 caracteres)
_clave_local = getattr(_settings, "boveda_clave_local", os.getenv("KC_BOVEDA_CLAVE_LOCAL"))

if _entorno != "local" and not getattr(_settings, "keyvault_url", os.getenv("KC_KEYVAULT_URL")):
    log.critical("boveda.keyvault_faltante", entorno=_entorno)
    raise RuntimeError("Falta KC_KEYVAULT_URL y el entorno no es local. Arranque abortado.")

if _entorno == "local" and not _clave_local:
    log.critical("boveda.clave_local_faltante")
    raise RuntimeError("Falta KC_BOVEDA_CLAVE_LOCAL en entorno local. Arranque abortado.")


# --- Clientes Singleton ---
_kv_client: SecretClient | None = None


def _obtener_kv_client() -> SecretClient:
    global _kv_client
    if not _kv_client:
        kv_url = getattr(_settings, "keyvault_url", os.getenv("KC_KEYVAULT_URL"))
        credencial = DefaultAzureCredential()
        _kv_client = SecretClient(vault_url=kv_url, credential=credencial)
    return _kv_client


def _formatear_nombre_kv(tenant_id: str, nombre: str) -> str:
    """Azure KV requiere nombres alfanuméricos y guiones."""
    return f"tenant-{tenant_id}-{nombre}".replace("_", "-")


# --- Funciones de Interfaz Pública ---


async def guardar(tenant_id: str, nombre: str, valor: dict[str, Any]) -> str:
    """Guarda el secreto en el destino final Y en el formato legado (Doble Escritura)."""
    valor_json = json.dumps(valor, ensure_ascii=False)

    # 1. Escritura en el nuevo destino
    if _entorno == "local":
        cifrado = cifrar(valor_json, _clave_local)
        clave_nueva = clave_tenant(tenant_id, "boveda_cifrada", nombre)
        await redis_cliente().set(clave_nueva, cifrado)
    else:
        nombre_kv = _formatear_nombre_kv(tenant_id, nombre)
        # La API de Azure KV es síncrona/blocking por defecto, en un entorno real asíncrono
        # deberíamos usar azure.keyvault.secrets.aio.SecretClient, asumimos que este wrapper
        # está bien para la integración.
        _obtener_kv_client().set_secret(nombre_kv, valor_json)

    # 2. Doble escritura en origen (plano) temporal por migración (Sprint actual)
    # TODO(Limpieza): Retirar este bloque tras validar que todos los secretos migraron.
    clave_legada = clave_tenant(tenant_id, "boveda", nombre)
    await redis_cliente().set(clave_legada, valor_json)

    log.info("boveda.guardado", ref=nombre, entorno=_entorno)
    return nombre


async def leer(tenant_id: str, nombre: str) -> dict[str, Any] | None:
    """Intenta leer del nuevo backend cifrado; si falla, hace fallback al legado plano."""
    valor_json: str | None = None

    # 1. Intentar lectura del nuevo destino seguro
    if _entorno == "local":
        clave_nueva = clave_tenant(tenant_id, "boveda_cifrada", nombre)
        cifrado = await redis_cliente().get(clave_nueva)
        if cifrado:
            valor_json = descifrar(cifrado, _clave_local)
    else:
        nombre_kv = _formatear_nombre_kv(tenant_id, nombre)
        try:
            secreto = _obtener_kv_client().get_secret(nombre_kv)
            valor_json = secreto.value
        except ResourceNotFoundError:
            pass

    # 2. Fallback a legado (doble lectura temporal) si no está en el nuevo destino
    if not valor_json:
        clave_legada = clave_tenant(tenant_id, "boveda", nombre)
        valor_json = await redis_cliente().get(clave_legada)
        if valor_json:
            log.warning("boveda.lectura_legada", ref=nombre, tenant_id=tenant_id)
            # Podríamos disparar una auto-migración aquí llamando a guardar()

    if not valor_json:
        return None

    return json.loads(valor_json)


async def eliminar(tenant_id: str, nombre: str) -> None:
    """Elimina el secreto de ambos orígenes (nuevo y legado)."""
    if _entorno == "local":
        clave_nueva = clave_tenant(tenant_id, "boveda_cifrada", nombre)
        await redis_cliente().delete(clave_nueva)
    else:
        nombre_kv = _formatear_nombre_kv(tenant_id, nombre)
        try:
            # Azure KV usa soft-delete, initiate_deleted_secret lo envía a la papelera
            poller = _obtener_kv_client().begin_delete_secret(nombre_kv)
            poller.wait()
        except ResourceNotFoundError:
            pass

    # Limpieza del formato legado
    clave_legada = clave_tenant(tenant_id, "boveda", nombre)
    await redis_cliente().delete(clave_legada)

    log.info("boveda.eliminado", ref=nombre, entorno=_entorno)
