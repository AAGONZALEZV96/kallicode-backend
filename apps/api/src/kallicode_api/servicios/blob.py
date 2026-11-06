"""Almacenamiento Blob de Kallicode.

Responsabilidades:

- Generar SAS de subida y descarga.
- Subir archivos desde procesos internos.
- Consultar propiedades reales.
- Descargar contenido para análisis antimalware.
- Eliminar objetos.

En local utiliza Azurite. En entornos gestionados, KC-BE-05-7 deberá
reemplazar la clave de cuenta por user delegation key y managed identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from azure.core.exceptions import (
    ResourceExistsError,
    ResourceNotFoundError,
)
from azure.storage.blob import (
    BlobSasPermissions,
    ContentSettings,
    generate_blob_sas,
)
from azure.storage.blob.aio import BlobServiceClient
from kallicode_core.config import get_settings

_CUENTA_AZURITE = "devstoreaccount1"

_CLAVE_AZURITE = (
    "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
)

_CONNECTION_STRING_AZURITE = (
    "DefaultEndpointsProtocol=http;"
    f"AccountName={_CUENTA_AZURITE};"
    f"AccountKey={_CLAVE_AZURITE};"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)


@dataclass(frozen=True)
class PropiedadesBlob:
    """Propiedades relevantes de un objeto almacenado."""

    tamano_bytes: int
    content_type: str


@dataclass(frozen=True)
class CredencialesCuenta:
    """Credenciales necesarias para generar un SAS de cuenta."""

    nombre: str
    clave: str


def _connection_string() -> str:
    """Obtiene la conexión apropiada para el entorno."""
    settings = get_settings()

    if settings.azure_storage_connection_string:
        return settings.azure_storage_connection_string

    if settings.env != "local":
        raise RuntimeError(
            "KC_AZURE_STORAGE_CONNECTION_STRING es obligatoria cuando KC_ENV no es local."
        )

    return _CONNECTION_STRING_AZURITE


def _parsear_connection_string(
    connection_string: str,
) -> dict[str, str]:
    """Convierte una cadena de conexión de Azure en un diccionario."""
    resultado: dict[str, str] = {}

    for componente in connection_string.split(";"):
        componente = componente.strip()

        if not componente or "=" not in componente:
            continue

        clave, valor = componente.split("=", 1)
        resultado[clave] = valor

    return resultado


def _credenciales_cuenta() -> CredencialesCuenta:
    """Obtiene nombre y clave para generar SAS.

    KC-BE-05-7 reemplazará esta estrategia por user delegation key en
    los entornos gestionados.
    """
    componentes = _parsear_connection_string(_connection_string())

    nombre = componentes.get("AccountName")
    clave = componentes.get("AccountKey")

    if not nombre or not clave:
        raise RuntimeError(
            "La cadena de conexión no contiene AccountName y AccountKey. "
            "La generación con managed identity se implementa en KC-BE-05-7."
        )

    return CredencialesCuenta(
        nombre=nombre,
        clave=clave,
    )


def _url_blob(
    ref: str,
    sas: str,
) -> str:
    """Construye la URL pública del blob sin alterar sus separadores."""
    settings = get_settings()
    ref_codificada = quote(
        ref,
        safe="/",
    )

    cuenta = settings.blob_account_url.rstrip("/")
    contenedor = quote(
        settings.blob_container_evidencia,
        safe="",
    )

    return f"{cuenta}/{contenedor}/{ref_codificada}?{sas}"


def url_subida(
    ref: str,
    content_type: str,
    minutos: int = 15,
) -> tuple[str, str]:
    """Genera un SAS que permite crear o sobrescribir un blob."""
    settings = get_settings()
    credenciales = _credenciales_cuenta()

    ahora = datetime.now(UTC)
    expira = ahora + timedelta(minutes=minutos)

    sas = generate_blob_sas(
        account_name=credenciales.nombre,
        container_name=settings.blob_container_evidencia,
        blob_name=ref,
        account_key=credenciales.clave,
        permission=BlobSasPermissions(
            create=True,
            write=True,
        ),
        start=ahora - timedelta(minutes=5),
        expiry=expira,
        content_type=content_type,
    )

    return (
        _url_blob(
            ref,
            sas,
        ),
        expira.isoformat(),
    )


def url_descarga(
    ref: str,
    minutos: int = 10,
    horas: int | None = None,
) -> tuple[str, str]:
    """Genera un SAS de sólo lectura."""
    settings = get_settings()
    credenciales = _credenciales_cuenta()

    ahora = datetime.now(UTC)

    duracion = timedelta(hours=horas) if horas is not None else timedelta(minutes=minutos)
    expira = ahora + duracion

    sas = generate_blob_sas(
        account_name=credenciales.nombre,
        container_name=settings.blob_container_evidencia,
        blob_name=ref,
        account_key=credenciales.clave,
        permission=BlobSasPermissions(
            read=True,
        ),
        start=ahora - timedelta(minutes=5),
        expiry=expira,
    )

    return (
        _url_blob(
            ref,
            sas,
        ),
        expira.isoformat(),
    )


async def _asegurar_contenedor(
    service: BlobServiceClient,
) -> None:
    """Crea el contenedor si todavía no existe."""
    settings = get_settings()

    cliente = service.get_container_client(settings.blob_container_evidencia)

    try:
        await cliente.create_container()
    except ResourceExistsError:
        pass


async def subir_archivo_servidor(
    ref: str,
    contenido: bytes,
    content_type: str = "text/csv",
) -> str:
    """Sube contenido desde la API o un worker."""
    settings = get_settings()

    async with BlobServiceClient.from_connection_string(_connection_string()) as service:
        await _asegurar_contenedor(service)

        cliente = service.get_blob_client(
            container=settings.blob_container_evidencia,
            blob=ref,
        )

        await cliente.upload_blob(
            contenido,
            overwrite=True,
            content_settings=ContentSettings(
                content_type=content_type,
            ),
        )

    return ref


async def propiedades(
    ref: str,
) -> PropiedadesBlob:
    """Consulta propiedades reales directamente desde Blob Storage."""
    settings = get_settings()

    async with BlobServiceClient.from_connection_string(_connection_string()) as service:
        cliente = service.get_blob_client(
            container=settings.blob_container_evidencia,
            blob=ref,
        )

        props = await cliente.get_blob_properties()

    content_type = props.content_settings.content_type or "application/octet-stream"
    content_type = content_type.split(";", 1)[0].strip().lower()

    return PropiedadesBlob(
        tamano_bytes=int(props.size),
        content_type=content_type,
    )


async def descargar_bytes(
    ref: str,
) -> bytes:
    """Descarga el contenido completo para análisis antimalware."""
    settings = get_settings()

    async with BlobServiceClient.from_connection_string(_connection_string()) as service:
        cliente = service.get_blob_client(
            container=settings.blob_container_evidencia,
            blob=ref,
        )

        descarga = await cliente.download_blob()
        contenido = await descarga.readall()

    return bytes(contenido)


async def eliminar(
    ref: str,
) -> bool:
    """Elimina un objeto; retorna False si ya no existía."""
    settings = get_settings()

    async with BlobServiceClient.from_connection_string(_connection_string()) as service:
        cliente = service.get_blob_client(
            container=settings.blob_container_evidencia,
            blob=ref,
        )

        try:
            await cliente.delete_blob(
                delete_snapshots="include",
            )
        except ResourceNotFoundError:
            return False

    return True


async def existe(
    ref: str,
) -> bool:
    """Indica si el objeto existe."""
    settings = get_settings()

    async with BlobServiceClient.from_connection_string(_connection_string()) as service:
        cliente = service.get_blob_client(
            container=settings.blob_container_evidencia,
            blob=ref,
        )

        return await cliente.exists()
