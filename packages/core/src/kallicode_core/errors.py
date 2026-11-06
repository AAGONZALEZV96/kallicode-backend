"""Catálogo de errores y sobre JSON estándar.

Toda respuesta de error tiene la forma:

{
    "error": {
        "codigo": "...",
        "mensaje": "...",
        "detalle": {},
        "trace_id": "...",
        "timestamp": "..."
    }
}

Los códigos son estables y programables por el Portal. Los errores 5xx
nunca deben exponer detalles internos.
"""

from __future__ import annotations

from typing import Any, Final

CATALOGO_ERRORES: Final[dict[str, dict[str, object]]] = {
    # Autenticación y autorización
    "NO_AUTENTICADO": {
        "http": 401,
        "descripcion": "Token ausente, inválido o expirado.",
    },
    "PERMISO_DENEGADO": {
        "http": 403,
        "descripcion": "El usuario no tiene permisos suficientes.",
    },
    "FUNCION_NO_DISPONIBLE": {
        "http": 403,
        "descripcion": "La función no está disponible para el plan actual.",
    },
    "AUTH_TOKEN_REVOCADO": {
        "http": 401,
        "descripcion": "El token fue revocado.",
    },
    "AUTH_TOKEN_AJENO": {
        "http": 403,
        "descripcion": "El token no pertenece al usuario actual.",
    },
    "AUTH_MULTIPLE_TENANTS": {
        "http": 409,
        "descripcion": "El correo pertenece a múltiples organizaciones.",
    },
    "AUTH_PASSWORD_DEBIL": {
        "http": 422,
        "descripcion": "La contraseña no cumple la política de seguridad.",
    },
    # Errores transversales
    "RECURSO_NO_ENCONTRADO": {
        "http": 404,
        "descripcion": "El recurso solicitado no existe.",
    },
    "VALIDACION_ENTRADA": {
        "http": 422,
        "descripcion": "La entrada no cumple las reglas de validación.",
    },
    "RATE_LIMIT_EXCEDIDO": {
        "http": 429,
        "descripcion": "Se superó el límite de peticiones.",
    },
    "DEPENDENCIA_NO_DISPONIBLE": {
        "http": 503,
        "descripcion": "Una dependencia externa no está disponible.",
    },
    "ERROR_INTERNO": {
        "http": 500,
        "descripcion": "Error interno del servidor.",
    },
    # Comercial
    "QUOTA_LOC_AGOTADA": {
        "http": 402,
        "descripcion": "Se agotó la cuota mensual de líneas de código.",
    },
    "ALMACENAMIENTO_AGOTADO": {
        "http": 402,
        "descripcion": "Se agotó la cuota de almacenamiento.",
    },
    "LIMITE_USUARIOS_PLAN": {
        "http": 402,
        "descripcion": "El plan alcanzó su máximo de usuarios.",
    },
    "PLAN_NO_SUPERIOR": {
        "http": 409,
        "descripcion": "El plan solicitado no es superior al actual.",
    },
    # Conexiones y proveedores
    "CONFIG_PROVEEDOR": {
        "http": 422,
        "descripcion": "La configuración del proveedor no es válida.",
    },
    "CREDENCIALES_INVALIDAS": {
        "http": 400,
        "descripcion": "Las credenciales no son válidas.",
    },
    "PERMISOS_EXCESIVOS": {
        "http": 400,
        "descripcion": "El proveedor concedió permisos excesivos.",
    },
    "CONEXION_DUPLICADA": {
        "http": 409,
        "descripcion": "Ya existe una conexión equivalente.",
    },
    # Archivos y adjuntos
    "TIPO_NO_PERMITIDO": {
        "http": 415,
        "descripcion": "El tipo de contenido no está permitido.",
    },
    "PAYLOAD_EXCESIVO": {
        "http": 413,
        "descripcion": "El payload supera el tamaño permitido.",
    },
    "ADJUNTO_DEMASIADO_GRANDE": {
        "http": 413,
        "descripcion": "El adjunto supera el tamaño máximo.",
    },
    "ADJUNTO_NO_ENCONTRADO": {
        "http": 404,
        "descripcion": "El adjunto no existe o ya fue asociado.",
    },
    "ADJUNTO_NO_LISTO": {
        "http": 409,
        "descripcion": "El adjunto todavía no está habilitado.",
    },
    "ADJUNTO_RECHAZADO": {
        "http": 409,
        "descripcion": "El adjunto fue rechazado.",
    },
    "SUBIDA_NO_CONFIRMADA": {
        "http": 409,
        "descripcion": "La subida no pudo confirmarse.",
    },
    "TAMANO_NO_COINCIDE": {
        "http": 409,
        "descripcion": "El tamaño real no coincide con el declarado.",
    },
    "TIPO_NO_COINCIDE": {
        "http": 409,
        "descripcion": "El tipo real no coincide con el declarado.",
    },
    "ADJUNTO_BLOQUEADO": {
        "http": 423,
        "descripcion": "El adjunto está pendiente o fue bloqueado.",
    },
    # Firmas
    "FIRMA_INVALIDA": {
        "http": 401,
        "descripcion": "La firma recibida no es válida.",
    },
    "FIRMA_NO_AUTORIZADA": {
        "http": 403,
        "descripcion": "La firma no está autorizada.",
    },
    # Tickets, jobs y gates
    "TICKET_REQUERIDO": {
        "http": 422,
        "descripcion": "La operación requiere un ticket.",
    },
    "TIMEZONE_INVALIDA": {
        "http": 422,
        "descripcion": "La zona horaria no es válida.",
    },
    "CICLO_INVALIDO": {
        "http": 422,
        "descripcion": "El ciclo indicado no es válido.",
    },
    "GATE_NO_ACTIVO": {
        "http": 409,
        "descripcion": "El gate no está activo.",
    },
    "TRANSICION_INVALIDA": {
        "http": 409,
        "descripcion": "La transición de estado no está permitida.",
    },
    "ARTEFACTO_FALTANTE": {
        "http": 409,
        "descripcion": "Falta un artefacto requerido.",
    },
    "LINEA_OCUPADA": {
        "http": 409,
        "descripcion": "La línea ya está ocupada.",
    },
    "LINEAS_EXCEDIDAS": {
        "http": 409,
        "descripcion": "Se superó el número de líneas permitido.",
    },
    "ETAPA_INCONSISTENTE": {
        "http": 409,
        "descripcion": "La etapa del ticket es inconsistente.",
    },
    "LOC_INCONSISTENTES": {
        "http": 422,
        "descripcion": "Los valores de LOC no coinciden.",
    },
    "FECHA_PASADA": {
        "http": 422,
        "descripcion": "La fecha indicada ya pasó.",
    },
    # Usuarios
    "USUARIO_NO_ENCONTRADO": {
        "http": 404,
        "descripcion": "El usuario no existe.",
    },
    "AUTODESACTIVACION": {
        "http": 400,
        "descripcion": "El usuario no puede desactivarse a sí mismo.",
    },
    "LIMITE_VISUALIZADORES": {
        "http": 409,
        "descripcion": "Se alcanzó el límite de visualizadores.",
    },
    "SPEC_NO_DISPONIBLE": {
        "http": 404,
        "descripcion": "El ticket todavía no tiene una especificación.",
    },
}


class AppError(Exception):
    """Error de aplicación con código estable y documentable."""

    def __init__(
        self,
        codigo: str,
        http: int,
        mensaje: str,
        detalle: dict[str, Any] | None = None,
    ):
        if codigo not in CATALOGO_ERRORES:
            raise ValueError(
                f"El código de error {codigo!r} no está registrado en CATALOGO_ERRORES."
            )

        http_catalogado = CATALOGO_ERRORES[codigo]["http"]

        if http_catalogado != http:
            raise ValueError(
                f"El código {codigo!r} declara HTTP {http}, "
                f"pero el catálogo declara HTTP {http_catalogado}."
            )

        self.codigo = codigo
        self.http = http
        self.mensaje = mensaje
        self.detalle = detalle

        super().__init__(f"{codigo}: {mensaje}")


def no_autenticado(
    msj: str = "Token ausente, inválido o expirado.",
) -> AppError:
    return AppError(
        "NO_AUTENTICADO",
        401,
        msj,
    )


def permiso_denegado(
    msj: str = "No tienes permisos para esta operación.",
) -> AppError:
    return AppError(
        "PERMISO_DENEGADO",
        403,
        msj,
    )


def funcion_no_disponible(
    msj: str = "Esta función no está incluida en tu plan.",
) -> AppError:
    return AppError(
        "FUNCION_NO_DISPONIBLE",
        403,
        msj,
    )


def no_encontrado(
    recurso: str = "El recurso",
) -> AppError:
    """RLS hace indistinguible un recurso ajeno de uno inexistente."""
    return AppError(
        "RECURSO_NO_ENCONTRADO",
        404,
        f"{recurso} no existe.",
    )


def conflicto(
    codigo: str,
    msj: str,
    detalle: dict[str, Any] | None = None,
) -> AppError:
    return AppError(
        codigo,
        409,
        msj,
        detalle,
    )


def validacion(
    msj: str,
    detalle: dict[str, Any] | None = None,
) -> AppError:
    return AppError(
        "VALIDACION_ENTRADA",
        422,
        msj,
        detalle,
    )


def rate_limit(
    retry_after_s: int = 60,
) -> AppError:
    return AppError(
        "RATE_LIMIT_EXCEDIDO",
        429,
        "Demasiadas peticiones. Espera un momento.",
        {
            "retry_after_s": retry_after_s,
        },
    )


def cuota_loc_agotada(
    detalle: dict[str, Any],
) -> AppError:
    return AppError(
        "QUOTA_LOC_AGOTADA",
        402,
        (
            "Cuota mensual de LOC agotada: los tickets nuevos "
            "quedan en cola hasta la renovación o upgrade."
        ),
        detalle,
    )


def dependencia_no_disponible(
    servicio: str,
) -> AppError:
    return AppError(
        "DEPENDENCIA_NO_DISPONIBLE",
        503,
        ("El servicio no está disponible en este momento; inténtalo de nuevo en unos minutos."),
        {
            "servicio": servicio,
        },
    )
