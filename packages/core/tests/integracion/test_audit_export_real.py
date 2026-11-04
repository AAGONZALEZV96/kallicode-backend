from __future__ import annotations

import socket
import pytest
from azure.storage.blob.aio import BlobServiceClient
from kallicode_api.servicios.blob import subir_archivo_servidor
from kallicode_core.config import get_settings


def _azurite_disponible() -> bool:
    """Comprueba si Azurite está corriendo localmente en el puerto 10000."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", 10000)) == 0


@pytest.mark.asyncio
async def test_subida_real_a_azurite_para_exportacion() -> None:
    # Si Azurite no está corriendo en el entorno (ej. en ciertos jobs del CI), 
    # omitimos la prueba real en lugar de romper el pipeline de forma fea.
    if not _azurite_disponible():
        pytest.skip("Azurite no está disponible localmente en el puerto 10000.")

    # 1. Definir los datos simulados de un archivo de exportación de auditoría
    ref_prueba = "tenant-test-123/exports/exp-test-999.csv"
    contenido_prueba = b"id,creado_en,evento\n1,2026-11-04,auditoria_generada\n"

    # 2. Ejecutar la función de subida real al Azurite que tienes corriendo localmente
    resultado_ref = await subir_archivo_servidor(
        ref=ref_prueba,
        contenido=contenido_prueba,
        content_type="text/csv",
    )

    assert resultado_ref == ref_prueba

    # 3. Verificar directamente contra Azurite utilizando el SDK de Azure para confirmar que el fichero existe físicamente
    s = get_settings()
    connection_string = (
        "DefaultEndpointsProtocol=http;"
        "AccountName=devstoreaccount1;"
        "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
        "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
    )

    async with BlobServiceClient.from_connection_string(connection_string) as client:
        container_client = client.get_container_client(s.blob_container_evidencia)
        blob_client = container_client.get_blob_client(ref_prueba)

        # Comprobar que el blob existe físicamente en Azurite
        assert await blob_client.exists()

        # Descargar y validar que el contenido sea idéntico
        descarga_stream = await blob_client.download_blob()
        datos_descargados = await descarga_stream.readall()
        assert datos_descargados == contenido_prueba

        # Limpieza del blob de prueba en Azurite
        await blob_client.delete_blob()