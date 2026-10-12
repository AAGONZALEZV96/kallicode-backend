"""Transformaciones comunes para publicar los contratos OpenAPI."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

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


DESCRIPCIONES_RESPUESTAS = {
    "400": "Solicitud inválida.",
    "401": "No autenticado.",
    "402": "Cuota agotada.",
    "403": "Permiso denegado.",
    "404": "Recurso no encontrado.",
    "409": "Conflicto de estado.",
    "413": "Payload demasiado grande.",
    "415": "Tipo de contenido no permitido.",
    "422": "Error de validación.",
    "429": "Límite de peticiones excedido.",
    "500": "Error interno.",
    "503": "Dependencia no disponible.",
}


def _esquema_error() -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["error"],
        "properties": {
            "error": {
                "type": "object",
                "required": [
                    "codigo",
                    "mensaje",
                    "trace_id",
                    "timestamp",
                ],
                "properties": {
                    "codigo": {
                        "type": "string",
                        "enum": sorted(CATALOGO_ERRORES),
                    },
                    "mensaje": {
                        "type": "string",
                    },
                    "detalle": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "trace_id": {
                        "type": "string",
                    },
                    "timestamp": {
                        "type": "string",
                        "format": "date-time",
                    },
                },
            }
        },
    }


def _respuesta_error(descripcion: str) -> dict[str, Any]:
    return {
        "description": descripcion,
        "content": {
            "application/json": {
                "schema": {
                    "$ref": "#/components/schemas/ErrorEnvelope",
                }
            }
        },
    }


def enriquecer_contrato(
    esquema: dict[str, Any],
    prefijo: str,
) -> dict[str, Any]:
    """Filtra una superficie y declara el sobre de error común."""

    contrato = deepcopy(esquema)
    contrato["paths"] = {
        ruta: operaciones
        for ruta, operaciones in contrato.get("paths", {}).items()
        if ruta.startswith(prefijo)
    }

    components = contrato.setdefault("components", {})
    schemas = components.setdefault("schemas", {})
    schemas["ErrorEnvelope"] = _esquema_error()

    for operaciones in contrato["paths"].values():
        for metodo, operacion in operaciones.items():
            if metodo.lower() not in METODOS_HTTP:
                continue

            respuestas = operacion.setdefault("responses", {})
            for codigo_http, descripcion in DESCRIPCIONES_RESPUESTAS.items():
                respuesta = respuestas.setdefault(
                    codigo_http,
                    {"description": descripcion},
                )
                respuesta["description"] = respuesta.get(
                    "description",
                    descripcion,
                )
                respuesta.setdefault("content", {})["application/json"] = {
                    "schema": {
                        "$ref": "#/components/schemas/ErrorEnvelope",
                    }
                }

    return contrato
