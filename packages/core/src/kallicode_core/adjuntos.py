"""Confirmación de adjuntos: propiedades reales, MIME y antimalware."""

from __future__ import annotations

import asyncio
import struct
from contextlib import asynccontextmanager

from azure.core import MatchConditions
from azure.identity.aio import ManagedIdentityCredential
from azure.storage.blob import ContentSettings
from azure.storage.blob.aio import BlobServiceClient
from sqlalchemy import text

from . import comercial
from .config import get_settings
from .db import uno
from .errors import AppError, dependencia_no_disponible, no_encontrado

TIPOS_ADJUNTO = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "video/mp4",
    "video/webm",
    "application/pdf",
    "text/markdown",
    "text/plain",
    "text/csv",
    "application/zip",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@asynccontextmanager
async def cliente_blob():
    """Abre el almacenamiento local o Azure con identidad administrada."""
    settings = get_settings()

    if settings.env == "local":
        if not settings.blob_connection_string:
            raise dependencia_no_disponible("almacenamiento")

        async with BlobServiceClient.from_connection_string(
            settings.blob_connection_string
        ) as cliente:
            yield cliente
    else:
        async with (
            ManagedIdentityCredential(
                client_id=settings.blob_managed_identity_client_id or None
            ) as credencial,
            BlobServiceClient(
                account_url=settings.blob_account_url,
                credential=credencial,
            ) as cliente,
        ):
            yield cliente


async def detectar_tipo(datos: bytes) -> str:
    """Detecta el contenido sin confiar en extensión ni Content-Type."""
    proceso = await asyncio.create_subprocess_exec(
        "file",
        "--brief",
        "--mime-type",
        "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        salida, _ = await asyncio.wait_for(
            proceso.communicate(datos),
            timeout=15,
        )
    except BaseException:
        if proceso.returncode is None:
            proceso.kill()
            await proceso.wait()
        raise

    if proceso.returncode:
        raise dependencia_no_disponible("detección de archivos")

    return salida.decode().strip()


async def analizar(datos: bytes) -> str:
    """Analiza el archivo mediante ClamAV INSTREAM."""
    settings = get_settings()
    writer = None

    try:
        async with asyncio.timeout(settings.clamav_timeout_seconds):
            reader, writer = await asyncio.open_connection(
                settings.clamav_host,
                settings.clamav_port,
            )

            writer.write(b"zINSTREAM\0")

            for posicion in range(0, len(datos), 65536):
                bloque = datos[posicion : posicion + 65536]
                writer.write(struct.pack("!I", len(bloque)) + bloque)
                await writer.drain()

            writer.write(b"\0\0\0\0")
            await writer.drain()

            respuesta = await reader.readuntil(b"\0")

            if respuesta.endswith(b" FOUND\0"):
                return "infectado"

            if respuesta == b"stream: OK\0":
                return "limpio"

            raise dependencia_no_disponible("antimalware")

    except (
        OSError,
        TimeoutError,
        asyncio.IncompleteReadError,
        asyncio.LimitOverrunError,
    ) as exc:
        raise dependencia_no_disponible("antimalware") from exc

    finally:
        if writer:
            writer.close()

            try:
                await writer.wait_closed()
            except OSError:
                pass


async def confirmar(
    db,
    tenant_id: str,
    adjunto_id: str,
) -> tuple[dict, AppError | None]:
    """Confirma el adjunto y retorna el posible rechazo.

    El llamador debe terminar correctamente la transacción antes de
    lanzar el error retornado, para conservar tamaño y bloqueo.
    """
    await comercial.bloquear_storage(db, tenant_id)

    adjunto = await uno(
        db,
        """
        SELECT *
          FROM core.ticket_attachments
         WHERE id = :a AND tenant_id = :t
         FOR UPDATE
        """,
        {"a": adjunto_id, "t": tenant_id},
    )

    if not adjunto:
        raise no_encontrado("El archivo")

    if adjunto["confirmado"]:
        return {
            "adjunto_id": adjunto_id,
            "confirmado": True,
            "escaneo": adjunto["escaneo"],
            "tamano_bytes": adjunto["tamano_bytes"],
            "content_type": adjunto["content_type"],
        }, None

    settings = get_settings()
    tipo = "application/octet-stream"
    escaneo = "pendiente"
    aviso = None
    error = None

    async with cliente_blob() as cliente:
        objeto = cliente.get_blob_client(
            settings.blob_container_evidencia,
            adjunto["blob_ref"],
        )

        try:
            propiedades = await objeto.get_blob_properties()
        except Exception as exc:
            raise dependencia_no_disponible("almacenamiento") from exc

        tamano = propiedades.size

        # El tamaño real se conserva incluso si se rechaza el objeto.
        await db.execute(
            text("""
                UPDATE core.ticket_attachments
                   SET tamano_bytes = :n,
                       content_type = :ct,
                       confirmado = false
                 WHERE id = :a AND tenant_id = :t
            """),
            {
                "n": tamano,
                "ct": tipo,
                "a": adjunto_id,
                "t": tenant_id,
            },
        )

        try:
            if tamano:
                cabecera = await objeto.download_blob(
                    offset=0,
                    length=min(tamano, 65536),
                    etag=propiedades.etag,
                    match_condition=MatchConditions.IfNotModified,
                )
                tipo = await detectar_tipo(await cabecera.readall())

            aviso = await comercial.verificar_storage(
                db,
                tenant_id,
                0,
            )

            if not 0 < tamano <= settings.max_adjunto_bytes:
                raise AppError(
                    "ADJUNTO_DEMASIADO_GRANDE",
                    413,
                    "Tamaño de archivo no permitido.",
                )

            descarga = await objeto.download_blob(
                etag=propiedades.etag,
                match_condition=MatchConditions.IfNotModified,
            )
            datos = await descarga.readall()

            if len(datos) != tamano:
                raise dependencia_no_disponible("almacenamiento")

            escaneo = await analizar(datos)
            tipo = await detectar_tipo(datos)

            if escaneo == "infectado":
                raise AppError(
                    "ADJUNTO_BLOQUEADO",
                    423,
                    "El archivo fue bloqueado por el análisis de seguridad.",
                )

            if tipo not in TIPOS_ADJUNTO:
                raise AppError(
                    "TIPO_NO_PERMITIDO",
                    415,
                    "El contenido del archivo no está permitido.",
                )

            await objeto.set_http_headers(
                ContentSettings(
                    content_type=tipo,
                    content_disposition="attachment",
                ),
                etag=propiedades.etag,
                match_condition=MatchConditions.IfNotModified,
            )

        except AppError as exc:
            error = exc

        except Exception:
            error = dependencia_no_disponible("verificación del adjunto")

    listo = error is None

    await db.execute(
        text("""
            UPDATE core.ticket_attachments
               SET content_type = :ct,
                   escaneo = :e,
                   confirmado = :listo
             WHERE id = :a AND tenant_id = :t
        """),
        {
            "ct": tipo,
            "e": escaneo,
            "listo": listo,
            "a": adjunto_id,
            "t": tenant_id,
        },
    )

    return {
        "adjunto_id": adjunto_id,
        "confirmado": listo,
        "escaneo": escaneo,
        "tamano_bytes": tamano,
        "content_type": tipo,
        "aviso": aviso,
    }, error
