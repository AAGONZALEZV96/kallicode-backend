"""Cliente de almacenamiento de evidencias para KalliCode con soporte para Azurite."""

import os

from azure.core.exceptions import ResourceExistsError
from azure.storage.blob.aio import BlobServiceClient

# Credenciales oficiales de Microsoft para el emulador local Azurite
AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"  # pragma: allowlist secret
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)


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
        buckets = ["evidencias-qa", "traces-playwright"]

        for bucket in buckets:
            container_client = self.client.get_container_client(bucket)
            try:
                await container_client.create_container()
            except ResourceExistsError:
                pass

        return buckets

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

    async def close(self) -> None:
        """Cierra la sesión HTTP asíncrona subyacente."""
        await self.client.close()
