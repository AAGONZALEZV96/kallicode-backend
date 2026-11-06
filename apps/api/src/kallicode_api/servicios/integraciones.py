"""Verificación de conexiones externas.

La simulación está permitida exclusivamente en local.
Fuera de local, un proveedor sin implementación real produce un error
explícito de configuración y nunca una conexión exitosa simulada.
"""

from __future__ import annotations

from kallicode_core.config import get_settings
from kallicode_core.errors import AppError
from kallicode_core.logging import log

_PERMISOS = {
    "github": ["repo:read", "pr:write", "webhooks:manage"],
    "gitlab": ["read_repository", "write_repository"],
    "jira": ["read:issues", "webhooks:manage"],
    "github_actions": ["workflows:read", "workflows:dispatch"],
    "postgresql": ["information_schema:read"],
}


async def probar(
    proveedor: str,
    config: dict,
    credenciales: dict,
) -> dict:
    """Verifica una conexión o informa que falta el adaptador real."""
    settings = get_settings()

    if settings.env != "local":
        log.warning(
            "integraciones.adaptador_no_configurado",
            proveedor=proveedor,
            entorno=settings.env,
        )
        raise AppError(
            "CONFIG_PROVEEDOR",
            422,
            "No hay una implementación real configurada para verificar este proveedor.",
            {"proveedor": proveedor},
        )

    if any(str(valor).lower() == "invalido" for valor in credenciales.values()):
        return {
            "ok": False,
            "mensaje": (f"No pudimos conectar con {proveedor}: credenciales rechazadas."),
        }

    usuario_bd = str(credenciales.get("usuario", "")).lower()

    if proveedor in (
        "postgresql",
        "mysql",
        "sqlserver",
        "oracle",
    ) and usuario_bd in (
        "root",
        "admin",
        "postgres",
        "sa",
    ):
        return {
            "ok": False,
            "permisos_excesivos": True,
            "mensaje": (
                "El usuario de base de datos tiene acceso a datos: "
                "crea uno restringido a information_schema."
            ),
        }

    log.info(
        "integraciones.test_simulado_local",
        proveedor=proveedor,
    )

    return {
        "ok": True,
        "mensaje": "Conexión simulada en entorno local.",
        "permisos": _PERMISOS.get(
            proveedor,
            ["basic"],
        ),
        "latencia_ms": 42,
    }
