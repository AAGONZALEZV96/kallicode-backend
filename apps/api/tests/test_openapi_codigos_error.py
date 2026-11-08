"""Comprueba el contrato de errores de ambas aplicaciones ASGI."""

from __future__ import annotations

import pytest
from kallicode_api.contrato_openapi import enriquecer_contrato
from kallicode_api.main import app as app_publica
from kallicode_api.main_interna import app as app_interna
from kallicode_core.errors import CATALOGO_ERRORES

METODOS_HTTP = frozenset(
    {
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "options",
        "head",
        "trace",
    }
)

SUPERFICIES = (
    ("/api/v1", app_publica),
    ("/internal/v1", app_interna),
)


@pytest.mark.parametrize("prefijo,aplicacion", SUPERFICIES)
def test_catalogo_de_errores_declarado_en_openapi(
    prefijo,
    aplicacion,
) -> None:
    contrato = enriquecer_contrato(
        aplicacion.openapi(),
        prefijo,
    )

    assert contrato.get("paths"), (
        f"No hay operaciones en la superficie {prefijo}."
    )

    envelope = contrato["components"]["schemas"]["ErrorEnvelope"]
    error = envelope["properties"]["error"]
    codigo_schema = error["properties"]["codigo"]

    codigos_declarados = set(codigo_schema["enum"])
    codigos_runtime = set(CATALOGO_ERRORES)

    assert codigos_declarados == codigos_runtime, (
        f"{prefijo} no declara exactamente el catálogo de errores. "
        f"Faltantes: {sorted(codigos_runtime - codigos_declarados)}. "
        f"Extra: {sorted(codigos_declarados - codigos_runtime)}."
    )


@pytest.mark.parametrize("prefijo,aplicacion", SUPERFICIES)
def test_cada_endpoint_declara_respuestas_de_error(
    prefijo,
    aplicacion,
) -> None:
    contrato = enriquecer_contrato(
        aplicacion.openapi(),
        prefijo,
    )

    assert contrato.get("paths"), (
        f"No hay operaciones en la superficie {prefijo}."
    )

    for ruta, operaciones in contrato["paths"].items():
        for metodo, operacion in operaciones.items():
            if metodo.lower() not in METODOS_HTTP:
                continue

            respuestas = operacion.get("responses", {})
            codigos_error = {
                str(status)
                for status in respuestas
                if str(status).startswith(("4", "5"))
            }

            assert codigos_error, (
                f"{metodo.upper()} {ruta} "
                "no declara respuestas 4xx/5xx."
            )


@pytest.mark.parametrize("prefijo,aplicacion", SUPERFICIES)
def test_respuestas_de_error_referencian_error_envelope(
    prefijo,
    aplicacion,
) -> None:
    contrato = enriquecer_contrato(
        aplicacion.openapi(),
        prefijo,
    )

    assert contrato.get("paths"), (
        f"No hay operaciones en la superficie {prefijo}."
    )

    for ruta, operaciones in contrato["paths"].items():
        for metodo, operacion in operaciones.items():
            if metodo.lower() not in METODOS_HTTP:
                continue

            for status, respuesta in operacion.get(
                "responses",
                {},
            ).items():
                if not str(status).startswith(("4", "5")):
                    continue

                contenido = respuesta.get("content", {})
                json_response = contenido.get(
                    "application/json",
                    {},
                )
                schema = json_response.get("schema", {})

                assert schema.get("$ref") == (
                    "#/components/schemas/ErrorEnvelope"
                ), (
                    f"{metodo.upper()} {ruta} respuesta {status} "
                    "no utiliza ErrorEnvelope."
                )