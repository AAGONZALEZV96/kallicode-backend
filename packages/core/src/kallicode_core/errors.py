"""Catálogo de errores y sobre JSON estándar."""

from __future__ import annotations

from typing import Any, Final

CATALOGO_ERRORES: Final[dict[str, dict[str, object]]] = {
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
    "QUOTA_LOC_AGOTADA": {
        "http": 402,
        "descripcion": "Se agotó la cuota mensual de líneas de código.",
    },
    "DEPENDENCIA_NO_DISPONIBLE": {
        "http": 503,
        "descripcion": "Una dependencia externa no está disponible.",
    },
    "ERROR_INTERNO": {
        "http": 500,
        "descripcion": "Error interno del servidor.",
    },
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
    "TIPO_NO_PERMITIDO": {
        "http": 415,
        "descripcion": "El tipo de contenido no está permitido.",
    },
    "PAYLOAD_EXCESIVO": {
        "http": 413,
        "descripcion": "El payload supera el tamaño permitido.",
    },
    "FIRMA_INVALIDA": {
        "http": 401,
        "descripcion": "La firma recibida no es válida.",
    },
    "FIRMA_NO_AUTORIZADA": {
        "http": 403,
        "descripcion": "La firma no está autorizada.",
    },
    "AUTH_TOKEN_REVOCADO": {
        "http": 401,
        "descripcion": "El token fue revocado.",
    },
    "AUTH_TOKEN_AJENO": {
        "http": 403,
        "descripcion": "El token no pertenece al usuario actual.",
    },
    "PLAN_NO_SUPERIOR": {
        "http": 409,
        "descripcion": "El plan solicitado no es superior al actual.",
    },
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
    "VEREDICTO_INCONSISTENTE": {
        "http": 422,
        "descripcion": "blocked sin hallazgos bloqueantes (o viceversa).",
    },
    "SCOPE_PROHIBIDO": {
        "http": 422,
        "descripcion": "scope_paths incluye rutas vetadas por la política del tenant.",
    },
    "MATRIZ_INCOMPLETA": {
        "http": 422,
        "descripcion": "Faltan criterios de aceptación en la matriz de QA.",
    },
    "ANOMALIA_SIN_ANALISIS": {
        "http": 422,
        "descripcion": "resultado=anomalia requiere el análisis y la recomendación.",
    },
    "FECHA_PASADA": {
        "http": 422,
        "descripcion": "La fecha indicada ya pasó.",
    },
    "ADJUNTO_BLOQUEADO": {
        "http": 423,
        "descripcion": "Adjunto pendiente o bloqueado por seguridad.",
    },
    "ADJUNTO_NO_ENCONTRADO": {
        "http": 404,
        "descripcion": "El adjunto no está disponible.",
    },
    "ALMACENAMIENTO_AGOTADO": {
        "http": 402,
        "descripcion": "Cuota de almacenamiento excedida.",
    },
    "ADJUNTO_DEMASIADO_GRANDE": {
        "http": 413,
        "descripcion": "El adjunto supera el tamaño máximo.",
    },
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
    "AUTH_MULTIPLE_TENANTS": {
        "http": 409,
        "descripcion": "El correo pertenece a múltiples organizaciones.",
    },
    "AUTH_PASSWORD_DEBIL": {
        "http": 422,
        "descripcion": "La contraseña no cumple la política de seguridad.",
    },
    "SPEC_NO_DISPONIBLE": {
        "http": 404,
        "descripcion": "El ticket todavía no tiene especificación.",
    },
    "EVIDENCIA_NO_DISPONIBLE": {
        "http": 404,
        "descripcion": "El ticket todavía no tiene evidencia QA.",
    },
    "SECURITY_NO_DISPONIBLE": {
        "http": 404,
        "descripcion": "El ticket todavía no pasó por seguridad.",
    },
    "GRAFO_NO_DISPONIBLE": {
        "http": 503,
        "descripcion": "El análisis de impacto no está disponible.",
    },
    "LIMITE_CREACION": {
        "http": 409,
        "descripcion": "Se alcanzó el límite de creación de tickets.",
    },
    "TICKET_YA_EN_PIPELINE": {
        "http": 409,
        "descripcion": "El ticket ya está en el pipeline.",
    },
    "CANCELACION_TARDIA": {
        "http": 409,
        "descripcion": "No se puede cancelar durante el despliegue.",
    },
    "CREADOR_OBLIGATORIO": {
        "http": 409,
        "descripcion": "El creador debe permanecer como visualizador.",
    },
    "LIMITE_USUARIOS_PLAN": {
        "http": 402,
        "descripcion": "Se alcanzó el cupo de usuarios del plan.",
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
                f"El código {codigo!r} declara HTTP {http}, pero el catálogo "
                f"declara HTTP {http_catalogado}."
            )

        self.codigo = codigo
        self.http = http
        self.mensaje = mensaje
        self.detalle = detalle

        super().__init__(f"{codigo}: {mensaje}")


def no_autenticado(
    msj: str = "Token ausente, inválido o expirado.",
) -> AppError:
    return AppError("NO_AUTENTICADO", 401, msj)


def permiso_denegado(
    msj: str = "No tienes permisos para esta operación.",
) -> AppError:
    return AppError("PERMISO_DENEGADO", 403, msj)


def funcion_no_disponible(
    msj: str = "Esta función no está incluida en tu plan.",
) -> AppError:
    return AppError("FUNCION_NO_DISPONIBLE", 403, msj)


def no_encontrado(
    recurso: str = "El recurso",
) -> AppError:
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
    return AppError(codigo, 409, msj, detalle)


def validacion(
    msj: str,
    detalle: dict[str, Any] | None = None,
) -> AppError:
    return AppError("VALIDACION_ENTRADA", 422, msj, detalle)


def rate_limit(
    retry_after_s: int = 60,
) -> AppError:
    return AppError(
        "RATE_LIMIT_EXCEDIDO",
        429,
        "Demasiadas peticiones. Espera un momento.",
        {"retry_after_s": retry_after_s},
    )


def cuota_loc_agotada(
    detalle: dict[str, Any],
) -> AppError:
    return AppError(
        "QUOTA_LOC_AGOTADA",
        402,
        "Cuota mensual de LOC agotada: los tickets nuevos quedan en cola "
        "hasta la renovación o upgrade.",
        detalle,
    )


def dependencia_no_disponible(
    servicio: str,
) -> AppError:
    return AppError(
        "DEPENDENCIA_NO_DISPONIBLE",
        503,
        "El servicio no está disponible en este momento; inténtalo de nuevo en unos minutos.",
        {"servicio": servicio},
    )
