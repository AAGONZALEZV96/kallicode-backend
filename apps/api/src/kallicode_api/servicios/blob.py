"""Blob storage: URLs firmadas de subida y descarga y subida desde el servidor.

Local: URLs pseudo-firmadas contra Azurite (o dummy) y subida de archivos reales
al emulador local de Azurite. El contrato es el mismo que en producción: nunca se
sirve el binario a través de la API.
TODO(produccion): generar SAS reales con azure-storage-blob + user
delegation key (managed identity).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient
from kallicode_core.config import get_settings

_CLAVE_DEV = b"kc-dev-firma"


def _firmar(ref: str, exp: datetime, verbo: str) -> str:
    base = f"{verbo}:{ref}:{int(exp.timestamp())}"
    return hmac.new(_CLAVE_DEV, base.encode(), hashlib.sha256).hexdigest()[:32]


def url_subida(ref: str, content_type: str, minutos: int = 15) -> tuple[str, str]:
    """Devuelve (upload_url, expira_en ISO). PUT directo del cliente al blob."""
    s = get_settings()
    exp = datetime.now(UTC) + timedelta(minutes=minutos)
    firma = _firmar(ref, exp, "put")
    url = (
        f"{s.blob_account_url}/{s.blob_container_evidencia}/{ref}"
        f"?sv=dev&se={int(exp.timestamp())}&sig={firma}"
    )
    return url, exp.isoformat()


def url_descarga(ref: str, minutos: int = 10, horas: int | None = None) -> tuple[str, str]:
    """Devuelve (download_url, expira_en ISO) de solo lectura."""
    s = get_settings()
    delta = timedelta(hours=horas) if horas else timedelta(minutes=minutos)
    exp = datetime.now(UTC) + delta
    firma = _firmar(ref, exp, "get")
    url = (
        f"{s.blob_account_url}/{s.blob_container_evidencia}/{ref}"
        f"?sv=dev&se={int(exp.timestamp())}&sig={firma}"
    )
    return url, exp.isoformat()


async def subir_archivo_servidor(ref: str, contenido: bytes, content_type: str = "text/csv") -> str:
    """Sube un archivo de forma asíncrona al blob desde el backend (utilizado por workers
    para exportaciones de auditoría). Compatible con Azurite en local y Azure Blob en producción.
    """
    s = get_settings()

    # En desarrollo local contra Azurite usa la cadena predeterminada del emulador si no hay setting
    connection_string = getattr(s, "azure_storage_connection_string", None)
    if not connection_string:
        connection_string = (
            "DefaultEndpointsProtocol=http;"
            "AccountName=devstoreaccount1;"
            "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
            "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
        )

    async with BlobServiceClient.from_connection_string(connection_string) as blob_service_client:
        container_client = blob_service_client.get_container_client(s.blob_container_evidencia)

        # Garantizar la existencia del contenedor en local/test
        try:
            await container_client.create_container()
        except Exception:
            pass

        blob_client = container_client.get_blob_client(ref)

        # Subida asíncrona de los bytes reales usando ContentSettings correctamente
        await blob_client.upload_blob(
            contenido, overwrite=True, content_settings=ContentSettings(content_type=content_type)
        )

        return ref
