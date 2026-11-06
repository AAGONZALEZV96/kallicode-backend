"""KC-BE-05-7: SAS reales contra Azurite."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
import pytest
from kallicode_api.servicios import blob

pytestmark = pytest.mark.integracion


def manipular_firma(url: str) -> str:
    partes = urlsplit(url)
    parametros = dict(parse_qsl(partes.query))

    firma = parametros["sig"]
    parametros["sig"] = ("B" if firma[0] == "A" else "A") + firma[1:]

    return urlunsplit(
        (
            partes.scheme,
            partes.netloc,
            partes.path,
            urlencode(parametros),
            partes.fragment,
        )
    )


async def test_sas_subida_descarga_y_firma_manipulada():
    ref = f"test-sas/{uuid4().hex}/archivo.txt"
    contenido = b"Prueba de SAS real contra Azurite."

    try:
        # Prepara el contenedor y el objeto sin depender de datos del Portal.
        await blob.subir_archivo_servidor(
            ref,
            b"inicial",
            "text/plain",
        )

        subida, _ = blob.url_subida(ref, "text/plain")

        async with httpx.AsyncClient(timeout=30) as cliente:
            respuesta = await cliente.put(
                subida,
                content=contenido,
                headers={
                    "x-ms-blob-type": "BlockBlob",
                    "Content-Type": "text/plain",
                },
            )
            assert respuesta.status_code == 201, respuesta.text

            descarga, _ = blob.url_descarga(ref)

            respuesta = await cliente.get(descarga)
            assert respuesta.status_code == 200, respuesta.text
            assert respuesta.content == contenido

            respuesta = await cliente.get(manipular_firma(descarga))
            assert respuesta.status_code == 403, respuesta.text

            respuesta = await cliente.put(
                manipular_firma(subida),
                content=b"No debe guardarse",
                headers={
                    "x-ms-blob-type": "BlockBlob",
                    "Content-Type": "text/plain",
                },
            )
            assert respuesta.status_code == 403, respuesta.text

            # El intento inválido no debe modificar el objeto.
            respuesta = await cliente.get(descarga)
            assert respuesta.status_code == 200, respuesta.text
            assert respuesta.content == contenido

    finally:
        await blob.eliminar(ref)
