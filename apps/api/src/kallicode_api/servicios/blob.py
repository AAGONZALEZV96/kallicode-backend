"""Almacenamiento Blob: SAS y subida de archivos desde el servidor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from azure.core.exceptions import ResourceExistsError
from azure.identity import ManagedIdentityCredential
from azure.storage.blob import BlobServiceClient, ContentSettings, generate_blob_sas
from kallicode_core.adjuntos import cliente_blob
from kallicode_core.config import get_settings
from kallicode_core.errors import dependencia_no_disponible


def _url(
    ref: str,
    permiso: str,
    duracion: timedelta,
) -> tuple[str, str]:
    settings = get_settings()
    ahora = datetime.now(UTC)
    expira = ahora + duracion

    if settings.env == "local":
        if not settings.blob_connection_string:
            raise dependencia_no_disponible("almacenamiento")

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
    else:
        credencial = ManagedIdentityCredential(
            client_id=settings.blob_managed_identity_client_id or None
        )

        try:
            with BlobServiceClient(
                account_url=settings.blob_account_url,
                credential=credencial,
            ) as cliente:
                clave_delegacion = cliente.get_user_delegation_key(
                    key_start_time=ahora - timedelta(minutes=5),
                    key_expiry_time=expira,
                )

                firma = generate_blob_sas(
                    account_name=cliente.account_name,
                    container_name=settings.blob_container_evidencia,
                    blob_name=ref,
                    user_delegation_key=clave_delegacion,
                    permission=permiso,
                    expiry=expira,
                )

                url = cliente.get_blob_client(
                    settings.blob_container_evidencia,
                    ref,
                ).url
        finally:
            credencial.close()

    return f"{url}?{firma}", expira.isoformat()


def verificar_delegacion() -> None:
    """Comprueba que la identidad puede obtener una clave de delegación."""
    settings = get_settings()

    credencial = ManagedIdentityCredential(
        client_id=settings.blob_managed_identity_client_id or None
    )

    try:
        with BlobServiceClient(
            account_url=settings.blob_account_url,
            credential=credencial,
        ) as cliente:
            ahora = datetime.now(UTC)

            cliente.get_user_delegation_key(
                key_start_time=ahora - timedelta(minutes=5),
                key_expiry_time=ahora + timedelta(minutes=5),
            )
    finally:
        credencial.close()


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
        )

    return ref
