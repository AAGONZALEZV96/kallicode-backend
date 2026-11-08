"""Almacenamiento Blob: SAS y subida de archivos desde el servidor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import (
    BlobServiceClient,
    ContentSettings,
    generate_blob_sas,
)
from azure.storage.blob.aio import (
    BlobServiceClient as AsyncBlobServiceClient,
)
from kallicode_core.config import get_settings
from kallicode_core.errors import dependencia_no_disponible


def _url(
    ref: str,
    permiso: str,
    duracion: timedelta,
) -> tuple[str, str]:
    settings = get_settings()

    if not settings.blob_connection_string:
        raise dependencia_no_disponible("almacenamiento")

    expira = datetime.now(UTC) + duracion

    with BlobServiceClient.from_connection_string(settings.blob_connection_string) as cliente:
        firma = generate_blob_sas(
            account_name=cliente.account_name,
            container_name=settings.blob_container_evidencia,
            blob_name=ref,
            account_key=cliente.credential.account_key,
            permission=permiso,
            expiry=expira,
        )

        url = cliente.get_blob_client(
            settings.blob_container_evidencia,
            ref,
        ).url

    return f"{url}?{firma}", expira.isoformat()


def url_subida(
    ref: str,
    content_type: str,
    minutos: int = 15,
) -> tuple[str, str]:
    """Permite crear el objeto, sin sobrescribir uno existente."""
    return _url(ref, "c", timedelta(minutes=minutos))


def url_descarga(
    ref: str,
    minutos: int = 10,
    horas: int | None = None,
) -> tuple[str, str]:
    """Genera una SAS de solo lectura."""
    duracion = timedelta(hours=horas) if horas is not None else timedelta(minutes=minutos)

    return _url(ref, "r", duracion)


async def subir_archivo_servidor(
    ref: str,
    contenido: bytes,
    content_type: str = "text/csv",
) -> str:
    """Sube archivos desde el backend, incluidas exportaciones de auditoría."""
    settings = get_settings()

    connection_string = settings.blob_connection_string or getattr(
        settings, "azure_storage_connection_string", None
    )

    if not connection_string and settings.env == "local":
        connection_string = (
            "DefaultEndpointsProtocol=http;"
            "AccountName=devstoreaccount1;"
            "AccountKey="
            "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
            "K1SZFPTOtr/KBHBeksoGMGw==;"
            "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
        )

    if not connection_string:
        raise dependencia_no_disponible("almacenamiento")

    async with AsyncBlobServiceClient.from_connection_string(connection_string) as cliente:
        contenedor = cliente.get_container_client(settings.blob_container_evidencia)

        try:
            await contenedor.create_container()
        except ResourceExistsError:
            pass

        objeto = contenedor.get_blob_client(ref)

        await objeto.upload_blob(
            contenido,
            overwrite=True,
            content_settings=ContentSettings(content_type=content_type),
        )

    return ref
