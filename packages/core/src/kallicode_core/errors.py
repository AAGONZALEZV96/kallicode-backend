"""Catálogo de errores y sobre JSON estándar (§3.6 del diseño de endpoints).

Toda respuesta de error tiene la forma:
    { "error": { "codigo", "mensaje", "detalle?", "trace_id", "timestamp" } }

- `codigo` es estable y programable por el frontend.
- `mensaje` está localizado (español por defecto) y es apto para pantalla.
- `detalle` es opcional y estructurado.
Los 5xx nunca exponen internals: el stack queda en el log correlacionado
por trace_id.
"""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Error de aplicación con código estable y mensaje para el usuario."""

    def __init__(self, codigo: str, http: int, mensaje: str,
                 detalle: dict[str, Any] | None = None):
        self.codigo = codigo
        self.http = http
        self.mensaje = mensaje
        self.detalle = detalle
        super().__init__(f"{codigo}: {mensaje}")


# ---------------------------------------------------------------------------
# Catálogo global (§18 del diseño). Los específicos se lanzan con AppError
# directamente desde cada módulo, siempre con código UPPER_SNAKE estable.
# ---------------------------------------------------------------------------
def no_autenticado(msj: str = "Token ausente, inválido o expirado.") -> AppError:
    return AppError("NO_AUTENTICADO", 401, msj)


def permiso_denegado(msj: str = "No tienes permisos para esta operación.") -> AppError:
    return AppError("PERMISO_DENEGADO", 403, msj)


def funcion_no_disponible(msj: str = "Esta función no está incluida en tu plan.") -> AppError:
    return AppError("FUNCION_NO_DISPONIBLE", 403, msj)


def no_encontrado(recurso: str = "El recurso") -> AppError:
    # RLS: un recurso de otro tenant es indistinguible de uno inexistente.
    return AppError("RECURSO_NO_ENCONTRADO", 404, f"{recurso} no existe.")


def conflicto(codigo: str, msj: str, detalle: dict | None = None) -> AppError:
    return AppError(codigo, 409, msj, detalle)


def validacion(msj: str, detalle: dict | None = None) -> AppError:
    return AppError("VALIDACION_ENTRADA", 422, msj, detalle)


def rate_limit(retry_after_s: int = 60) -> AppError:
    return AppError("RATE_LIMIT_EXCEDIDO", 429,
                    "Demasiadas peticiones. Espera un momento.",
                    {"retry_after_s": retry_after_s})


def cuota_loc_agotada(detalle: dict) -> AppError:
    return AppError("QUOTA_LOC_AGOTADA", 402,
                    "Cuota mensual de LOC agotada: los tickets nuevos quedan en cola "
                    "hasta la renovación o upgrade.", detalle)


def dependencia_no_disponible(servicio: str) -> AppError:
    return AppError("DEPENDENCIA_NO_DISPONIBLE", 503,
                    "El servicio no está disponible en este momento; "
                    "inténtalo de nuevo en unos minutos.",
                    {"servicio": servicio})
