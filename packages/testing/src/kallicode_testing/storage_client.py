"""Cliente de almacenamiento de evidencias para KalliCode con soporte para Azurite."""

from __future__ import annotations

import os
from typing import Any

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob.aio import BlobServiceClient

# Credenciales oficiales de Microsoft para el emulador local Azurite
AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"  # pragma: allowlist secret
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)


class WORMProtectedBlobClient:
    """Proxy de BlobClient con políticas WORM activas para prevención de mutaciones."""

    def __init__(self, inner_client: Any) -> None:
        self._inner = inner_client

    @property
    def url(self) -> str:
        return str(self._inner.url)

    async def exists(self) -> bool:
        return bool(await self._inner.exists())

    async def download_blob(self, *args: Any, **kwargs: Any) -> Any:
        return await self._inner.download_blob(*args, **kwargs)

    async def delete_blob(self, *args: Any, **kwargs: Any) -> None:
        from kallicode_testing.evidence_sealer import WORMImmutabilityViolationError

        raise WORMImmutabilityViolationError(
            "409 Conflict: Operación DELETE rechazada por política WORM. El artefacto es inmutable."
        )

    async def upload_blob(self, data: Any, *args: Any, **kwargs: Any) -> Any:
        from kallicode_testing.evidence_sealer import WORMImmutabilityViolationError

        if kwargs.get("overwrite") is True:
            raise WORMImmutabilityViolationError(
                "409 Conflict: Operación PUT con overwrite=True rechazada por política WORM."
            )
        if await self._inner.exists():
            raise WORMImmutabilityViolationError(
                "409 Conflict: Operación PUT rechazada. El blob ya existe y es estrictamente inmutable."
            )
        return await self._inner.upload_blob(data, *args, **kwargs)


class QAStorageClient:
    """Cliente de almacenamiento de evidencias y traces para el motor QA."""

    def __init__(self, connection_string: str | None = None) -> None:
        """Inicializa el cliente conmutando a Azurite según el entorno."""
        is_emulator = os.getenv("KC_STORAGE_EMULATOR", "false").lower() == "true"

        if is_emulator:
            self.conn_str = AZURITE_CONNECTION_STRING
        else:
            self.conn_str = connection_string or os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
            if not self.conn_str:
                raise ValueError("No se proporcionó cadena de conexión para entorno real de Azure.")

        self.client = BlobServiceClient.from_connection_string(self.conn_str)

    async def initialize_buckets(self) -> list[str]:
        """Crea los contenedores base requeridos por el motor QA si no existen."""
        buckets = ["evidencias-qa", "evidence-qa", "traces-playwright"]

        for bucket in buckets:
            container_client = self.client.get_container_client(bucket)
            try:
                await container_client.create_container()
            except ResourceExistsError:
                pass

        return buckets

    def get_worm_blob_client(self, bucket_name: str, blob_name: str) -> WORMProtectedBlobClient:
        """Obtiene un cliente de blob protegido por la política WORM."""
        inner = self.client.get_blob_client(container=bucket_name, blob=blob_name)
        return WORMProtectedBlobClient(inner)

    async def upload_file(self, bucket_name: str, blob_name: str, data: bytes) -> str:
        """Sube un archivo en formato binario al bucket especificado."""
        blob_client = self.client.get_blob_client(container=bucket_name, blob=blob_name)
        await blob_client.upload_blob(data, overwrite=True)
        return blob_client.url

    async def download_file(self, bucket_name: str, blob_name: str) -> bytes:
        """Descarga el contenido binario de un blob específico."""
        blob_client = self.client.get_blob_client(container=bucket_name, blob=blob_name)
        stream = await blob_client.download_blob()
        return await stream.readall()

    async def delete_file(
        self, bucket_name: str, blob_name: str, enforce_worm: bool = True
    ) -> None:
        """
        Elimina un archivo si no está protegido por WORM.
        Si enforce_worm es True o el bucket es de evidencias, rechaza con WORMImmutabilityViolationError.
        """
        from kallicode_testing.evidence_sealer import WORMImmutabilityViolationError

        if enforce_worm or "evidencia" in bucket_name.lower() or "evidence" in bucket_name.lower():
            raise WORMImmutabilityViolationError(
                f"409 Conflict: Operación DELETE rechazada por política WORM. "
                f"El blob '{blob_name}' en '{bucket_name}' es estrictamente inmutable."
            )
        blob_client = self.client.get_blob_client(container=bucket_name, blob=blob_name)
        await blob_client.delete_blob()

    async def close(self) -> None:
        """Cierra la sesión HTTP asíncrona subyacente."""
        await self.client.close()
