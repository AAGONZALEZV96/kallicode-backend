"""Trazas de origen de pasos LLM en Blob (§11.3 del diseño, KC-BE-08-4).

Cada paso LLM ejecutado por el orquestador persiste su entrada completa y su
salida en Blob bajo una ruta determinista por (tenant, ticket, step_id), con
metadatos de retención declarada (mínimo 365 días según D14 / core.retention_policies
para `core.llm_steps`) y cifrado en reposo gestionado por la plataforma.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from urllib.parse import quote

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import ContentSettings
from kallicode_core.adjuntos import cliente_blob
from kallicode_core.config import get_settings

PREFIJO_TRAZAS_LLM = "trazas-llm"
RETENCION_TRAZAS_LLM_MINIMA_DIAS = 365
CIFRADO_TRAZAS_LLM = "aes256_platform_managed"


def construir_refs_traza(
    tenant_id: str,
    ticket_ref: str,
    step_id: str,
) -> tuple[str, str]:
    """Devuelve las referencias deterministas (entrada_ref, salida_ref) en Blob."""
    tenant = quote(str(tenant_id), safe="")
    ticket = quote(str(ticket_ref), safe="")
    step = quote(str(step_id), safe="")
    base = f"{PREFIJO_TRAZAS_LLM}/{tenant}/{ticket}/{step}"
    return f"{base}/entrada.json", f"{base}/salida.json"


async def subir_traza_blob(
    ref: str,
    contenido: bytes,
    *,
    tenant_id: str,
    ticket_ref: str,
    step_id: str,
    tipo: Literal["entrada", "salida"],
    content_type: str = "application/json",
) -> str:
    """Sube un payload de traza LLM a Blob con metadatos de retención y cifrado."""
    settings = get_settings()
    async with cliente_blob() as cliente:
        contenedor = cliente.get_container_client(settings.blob_container_evidencia)
        if settings.env == "local":
            try:
                await contenedor.create_container()
            except ResourceExistsError:
                pass
        objeto = contenedor.get_blob_client(ref)
        await objeto.upload_blob(
            contenido,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
            metadata={
                "tenant_id": str(tenant_id),
                "ticket_id": str(ticket_ref),
                "step_id": str(step_id),
                "tipo_traza": tipo,
                "retencion_minima_dias": str(RETENCION_TRAZAS_LLM_MINIMA_DIAS),
                "cifrado": CIFRADO_TRAZAS_LLM,
            },
        )
    return ref


async def publicar_trazas_paso(
    *,
    tenant_id: str,
    ticket_ref: str,
    step_id: str,
    entrada: dict[str, Any],
    salida: dict[str, Any],
    subir_fn: Callable[..., Awaitable[str]] | None = None,
) -> tuple[str, str]:
    """Serializa y sube entrada y salida de un paso LLM a sus rutas deterministas."""
    entrada_ref, salida_ref = construir_refs_traza(tenant_id, ticket_ref, step_id)
    entrada_bytes = json.dumps(entrada, ensure_ascii=False, default=str).encode("utf-8")
    salida_bytes = json.dumps(salida, ensure_ascii=False, default=str).encode("utf-8")

    uploader = subir_fn or subir_traza_blob
    ref_in = await uploader(
        entrada_ref,
        entrada_bytes,
        tenant_id=tenant_id,
        ticket_ref=ticket_ref,
        step_id=step_id,
        tipo="entrada",
        content_type="application/json",
    )
    ref_out = await uploader(
        salida_ref,
        salida_bytes,
        tenant_id=tenant_id,
        ticket_ref=ticket_ref,
        step_id=step_id,
        tipo="salida",
        content_type="application/json",
    )
    return ref_in, ref_out
