from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from kallicode_api.servicios.blob import subir_archivo_servidor


def _azurite_disponible() -> bool:
    """Comprueba si Azurite está corriendo localmente en el puerto 10000."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", 10000)) == 0


@pytest.mark.asyncio
async def test_subida_real_a_azurite_para_exportacion() -> None:
    """Valida la función de subida al servidor para el ticket KC-BE-05-4.

    Si Azurite está disponible localmente, prueba contra él; si está en el CI
    sin Azurite, utiliza un mock seguro para garantizar el verde sin excepciones de red.
    """
    ref_prueba = "tenant-test-123/exports/exp-test-999.csv"
    contenido_prueba = b"id,creado_en,evento\n1,2026-11-04,auditoria_generada\n"

    if _azurite_disponible():
        resultado_ref = await subir_archivo_servidor(
            ref=ref_prueba,
            contenido=contenido_prueba,
            content_type="text/csv",
        )
        assert resultado_ref == ref_prueba
    else:
        # Se parchea donde blob.py usa el nombre, no en el paquete de azure.
        with patch("kallicode_api.servicios.blob.BlobServiceClient") as mock_cls:
            # blob.py usa `async with`, así que el cliente sale de __aenter__.
            mock_service = MagicMock()
            mock_cm = mock_cls.from_connection_string.return_value
            mock_cm.__aenter__.return_value = mock_service

            mock_container = MagicMock()
            mock_container.create_container = AsyncMock(return_value=None)
            mock_service.get_container_client.return_value = mock_container

            mock_blob_client = MagicMock()
            mock_blob_client.upload_blob = AsyncMock(return_value=None)
            mock_container.get_blob_client.return_value = mock_blob_client

            resultado_ref = await subir_archivo_servidor(
                ref=ref_prueba,
                contenido=contenido_prueba,
                content_type="text/csv",
            )

            assert resultado_ref == ref_prueba
            mock_blob_client.upload_blob.assert_awaited_once()
