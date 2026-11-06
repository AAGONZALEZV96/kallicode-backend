"""Confirmación de contenido, antimalware y publicación segura de adjuntos."""

from __future__ import annotations

import asyncio
import hashlib
import io
import tempfile
import zipfile

from azure.storage.blob.aio import BlobServiceClient
from kallicode_api.servicios import blob
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_tenant, uno
from kallicode_core.logging import log
from sqlalchemy import text


def _campo(campos: dict, nombre: str) -> str:
    valor = campos.get(nombre, campos.get(nombre.encode()))
    if valor is None:
        raise ValueError(f"Falta el campo {nombre}.")
    return valor.decode() if isinstance(valor, bytes) else str(valor)


async def analizar_con_clamav(contenido: bytes) -> tuple[bool, str]:
    settings = get_settings()

    async def ejecutar():
        reader, writer = await asyncio.open_connection(
            settings.clamav_host,
            settings.clamav_port,
        )
        try:
            writer.write(b"zINSTREAM\0")

            for inicio in range(0, len(contenido), 65_536):
                bloque = contenido[inicio : inicio + 65_536]
                writer.write(len(bloque).to_bytes(4, "big"))
                writer.write(bloque)
                await writer.drain()

            writer.write(b"\0\0\0\0")
            await writer.drain()
            respuesta = await reader.readuntil(b"\0")
        finally:
            writer.close()
            await writer.wait_closed()

        resultado = respuesta.decode(errors="replace").rstrip("\0")

        if resultado.endswith(" FOUND"):
            firma = resultado.split(":", 1)[-1]
            return False, firma.removesuffix(" FOUND").strip()

        if resultado.endswith(": OK"):
            return True, "OK"

        raise RuntimeError(f"Respuesta inesperada de ClamAV: {resultado!r}")

    return await asyncio.wait_for(
        ejecutar(),
        timeout=settings.clamav_timeout_s,
    )


async def _descargar_limitado(ref: str) -> bytes:
    """Limita la memoria incluso si el objeto cambió después de confirmar."""
    settings = get_settings()
    limite = settings.max_adjunto_bytes

    async with BlobServiceClient.from_connection_string(blob._connection_string()) as service:
        cliente = service.get_blob_client(
            container=settings.blob_container_evidencia,
            blob=ref,
        )
        descarga = await cliente.download_blob()

        contenido = bytearray()

        async for bloque in descarga.chunks():
            if len(contenido) + len(bloque) > limite:
                raise ValueError("ADJUNTO_DEMASIADO_GRANDE")
            contenido.extend(bloque)

    return bytes(contenido)


async def _detectar_tipo(contenido: bytes) -> str:
    """Usa libmagic mediante file; no confía en extensión ni cabecera HTTP."""
    with tempfile.TemporaryFile() as archivo:
        archivo.write(contenido)
        archivo.seek(0)

        proceso = await asyncio.create_subprocess_exec(
            "file",
            "--brief",
            "--mime-type",
            "-",
            stdin=archivo,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            salida, _ = await asyncio.wait_for(
                proceso.communicate(),
                timeout=10,
            )
        except BaseException:
            if proceso.returncode is None:
                proceso.kill()
            await proceso.wait()
            raise

    if proceso.returncode != 0:
        raise RuntimeError("No se pudo detectar el tipo del archivo.")

    tipo = salida.decode().strip().lower()

    # Algunas versiones de libmagic identifican DOCX como ZIP.
    if tipo == "application/zip":
        try:
            with zipfile.ZipFile(io.BytesIO(contenido)) as archivo_zip:
                nombres = set(archivo_zip.namelist())
                if {
                    "[Content_Types].xml",
                    "word/document.xml",
                }.issubset(nombres):
                    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        except zipfile.BadZipFile:
            return "application/octet-stream"

    return tipo


def _tipo_compatible(declarado: str, detectado: str) -> bool:
    # CSV y Markdown son texto; libmagic puede clasificarlos como text/plain.
    if declarado in {"text/plain", "text/markdown", "text/csv"}:
        return detectado in {"text/plain", "text/csv"}

    return declarado == detectado


async def _obtener(tenant_id: str, adjunto_id: str) -> dict | None:
    async with sesion_tenant(tenant_id, actor="svc:adjuntos") as db:
        return await uno(
            db,
            """
            SELECT id, blob_ref, estado, escaneo, tamano_bytes, content_type
            FROM core.ticket_attachments
            WHERE id = :a AND tenant_id = :t
            """,
            {"a": adjunto_id, "t": tenant_id},
        )


async def _rechazar(
    tenant_id: str,
    adjunto_id: str,
    motivo: str,
    *,
    infectado: bool = False,
    tipo_real: str | None = None,
) -> None:
    async with sesion_tenant(tenant_id, actor="svc:adjuntos") as db:
        await db.execute(
            text("""
                UPDATE core.ticket_attachments
                SET estado = 'rechazado',
                    escaneo = CASE
                        WHEN :infectado THEN 'infectado'
                        ELSE escaneo
                    END,
                    motivo_rechazo = :motivo,
                    content_type = coalesce(:tipo, content_type)
                WHERE id = :a
                  AND tenant_id = :t
                  AND estado = 'pendiente_escaneo'
            """),
            {
                "a": adjunto_id,
                "t": tenant_id,
                "motivo": motivo,
                "infectado": infectado,
                "tipo": tipo_real,
            },
        )


async def procesar(campos: dict) -> None:
    tenant_id = _campo(campos, "tenant_id")
    adjunto_id = _campo(campos, "adjunto_id")
    adjunto = await _obtener(tenant_id, adjunto_id)

    if adjunto is None:
        return

    if adjunto["estado"] in {"listo", "rechazado"}:
        return

    if adjunto["estado"] != "pendiente_escaneo":
        raise RuntimeError("El adjunto no está confirmado.")

    try:
        contenido = await _descargar_limitado(adjunto["blob_ref"])
    except ValueError:
        await _rechazar(
            tenant_id,
            adjunto_id,
            "ADJUNTO_DEMASIADO_GRANDE",
        )
        return

    if len(contenido) != adjunto["tamano_bytes"]:
        await _rechazar(
            tenant_id,
            adjunto_id,
            "OBJETO_MODIFICADO",
        )
        return

    # Se escanean exactamente los bytes que posteriormente se publicarán.
    limpio, firma = await analizar_con_clamav(contenido)

    if not limpio:
        await _rechazar(
            tenant_id,
            adjunto_id,
            "MALWARE_DETECTADO",
            infectado=True,
        )
        log.info(
            "attachments.escaneado",
            adjunto_id=adjunto_id,
            resultado="infectado",
            firma=firma,
        )
        return

    tipo_real = await _detectar_tipo(contenido)

    if not _tipo_compatible(adjunto["content_type"], tipo_real):
        await _rechazar(
            tenant_id,
            adjunto_id,
            "TIPO_NO_COINCIDE",
            tipo_real=tipo_real,
        )
        return

    # La URL SAS de subida autoriza únicamente la referencia original.
    # Nunca autoriza esta ruta de contenido verificado.
    digest = hashlib.sha256(contenido).hexdigest()
    ref_segura = f"{tenant_id}/verificados/{adjunto_id}/{digest}"

    await blob.subir_archivo_servidor(
        ref_segura,
        contenido,
        adjunto["content_type"],
    )

    async with sesion_tenant(tenant_id, actor="svc:adjuntos") as db:
        await db.execute(
            text("""
                UPDATE core.ticket_attachments
                SET estado = 'listo',
                    escaneo = 'limpio',
                    motivo_rechazo = NULL,
                    blob_ref = :ref
                WHERE id = :a
                  AND tenant_id = :t
                  AND estado = 'pendiente_escaneo'
                  AND escaneo = 'pendiente'
            """),
            {
                "a": adjunto_id,
                "t": tenant_id,
                "ref": ref_segura,
            },
        )

    log.info(
        "attachments.escaneado",
        adjunto_id=adjunto_id,
        resultado="limpio",
        tipo_detectado=tipo_real,
    )
