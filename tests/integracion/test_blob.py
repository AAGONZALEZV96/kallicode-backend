"""KC-BE-05-7: prueba de SAS reales contra Azurite."""

from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
import pytest
from azure.storage.blob import BlobServiceClient
from kallicode_api.servicios import blob
from kallicode_testing.storage_client import AZURITE_CONNECTION_STRING


@pytest.mark.integracion
def test_subida_descarga_y_firma_manipulada(monkeypatch):
    contenedor = f"prueba-{uuid4().hex}"
    referencia = "tenant-prueba/archivo.txt"
    contenido = b"Botin guardado correctamente"

    settings = SimpleNamespace(
        env="local",
        blob_connection_string=AZURITE_CONNECTION_STRING,
        blob_container_evidencia=contenedor,
    )
    monkeypatch.setattr(blob, "get_settings", lambda: settings)

    with BlobServiceClient.from_connection_string(AZURITE_CONNECTION_STRING) as servicio:
        almacen = servicio.get_container_client(contenedor)
        almacen.create_container()

        try:
            url_subida, _ = blob.url_subida(
                referencia,
                "text/plain",
            )

            respuesta_subida = httpx.put(
                url_subida,
                content=contenido,
                headers={
                    "x-ms-blob-type": "BlockBlob",
                    "Content-Type": "text/plain",
                },
                timeout=10,
            )

            assert respuesta_subida.status_code == 201

            url_descarga, _ = blob.url_descarga(referencia)

            respuesta_descarga = httpx.get(
                url_descarga,
                timeout=10,
            )

            assert respuesta_descarga.status_code == 200
            assert respuesta_descarga.content == contenido

            partes = urlsplit(url_descarga)
            parametros = parse_qsl(
                partes.query,
                keep_blank_values=True,
            )
            parametros_manipulados = [
                (clave, valor + "alterada" if clave == "sig" else valor)
                for clave, valor in parametros
            ]

            url_manipulada = urlunsplit(
                (
                    partes.scheme,
                    partes.netloc,
                    partes.path,
                    urlencode(parametros_manipulados),
                    partes.fragment,
                )
            )

            respuesta_manipulada = httpx.get(
                url_manipulada,
                timeout=10,
            )

            assert respuesta_manipulada.status_code == 403
        finally:
            almacen.delete_container()
