"""Pruebas de integración local para QAStorageClient contra Azurite."""

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from kallicode_testing.storage_client import QAStorageClient

# Habilita el modo asíncrono para todas las pruebas del archivo
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def setup_emulator_env() -> None:
    """Fuerza el uso del emulador para aislar las pruebas de la nube real."""
    os.environ["KC_STORAGE_EMULATOR"] = "true"


@pytest_asyncio.fixture
async def storage_client() -> AsyncGenerator[QAStorageClient, None]:
    """Provee una instancia del cliente y maneja su cierre seguro."""
    client = QAStorageClient()
    yield client
    await client.close()


async def test_initialize_buckets(storage_client: QAStorageClient) -> None:
    """Verifica la creación idempotente de los contenedores base."""
    buckets = await storage_client.initialize_buckets()
    assert "evidencias-qa" in buckets
    assert "traces-playwright" in buckets


async def test_upload_and_download_file(storage_client: QAStorageClient) -> None:
    """Verifica el flujo completo de I/O en almacenamiento local."""
    await storage_client.initialize_buckets()

    bucket = "evidencias-qa"
    blob_name = "test-evidence.txt"
    content = b"Evidencia simulada de falla en Playwright"

    # Prueba de subida
    url = await storage_client.upload_file(bucket, blob_name, content)
    assert "devstoreaccount1/evidencias-qa/test-evidence.txt" in url

    # Prueba de descarga
    downloaded_content = await storage_client.download_file(bucket, blob_name)
    assert downloaded_content == content
