"""Tests de conexiones con proveedores externos (GitHub, Jira, CI, BD cliente).

Local: simulación determinista — credencial con valor 'invalido' falla, un
usuario de BD llamado 'root'/'admin' dispara PERMISOS_EXCESIVOS; el resto
pasa. TODO(produccion): llamadas reales por proveedor (verificación de
scopes del PAT de GitHub, permisos del usuario information_schema, etc.).
"""
from __future__ import annotations

from kallicode_core.logging import log

_PERMISOS = {
    "github": ["repo:read", "pr:write", "webhooks:manage"],
    "gitlab": ["read_repository", "write_repository"],
    "jira": ["read:issues", "webhooks:manage"],
    "github_actions": ["workflows:read", "workflows:dispatch"],
    "postgresql": ["information_schema:read"],
}


async def probar(proveedor: str, config: dict, credenciales: dict) -> dict:
    """Devuelve {ok, mensaje, permisos, latencia_ms, permisos_excesivos?}."""
    if any(str(v).lower() == "invalido" for v in credenciales.values()):
        return {"ok": False,
                "mensaje": f"No pudimos conectar con {proveedor}: credenciales rechazadas."}
    usuario_bd = str(credenciales.get("usuario", "")).lower()
    if proveedor in ("postgresql", "mysql", "sqlserver", "oracle") and \
            usuario_bd in ("root", "admin", "postgres", "sa"):
        return {"ok": False, "permisos_excesivos": True,
                "mensaje": "El usuario de base de datos tiene acceso a datos: crea uno "
                           "restringido a information_schema."}
    log.info("integraciones.test_ok", proveedor=proveedor)
    return {"ok": True, "mensaje": "Conexión verificada.",
            "permisos": _PERMISOS.get(proveedor, ["basic"]), "latencia_ms": 42}
