"""Verificación real de conexión y permisos contra Jira Cloud/Server REST API."""

from __future__ import annotations

import time

import httpx

from ._base import ResultadoPrueba, crear_cliente_http, medir_ms


async def probar_jira(config: dict, credenciales: dict) -> ResultadoPrueba:
    base_url = str(config.get("url") or config.get("base_url") or "").strip().rstrip("/")
    if not base_url:
        return ResultadoPrueba(
            ok=False,
            mensaje="Falta la URL de la instancia de Jira en la configuración (ej. 'https://empresa.atlassian.net').",
        )

    email = str(credenciales.get("email") or credenciales.get("usuario") or "").strip()
    token = str(credenciales.get("token") or credenciales.get("api_token") or "").strip()
    if not token:
        return ResultadoPrueba(
            ok=False,
            mensaje="Falta el API token de Jira en las credenciales.",
        )

    auth = (email, token) if email else None
    headers = {
        "Accept": "application/json",
        "User-Agent": "kallicode-verifier/1.0",
    }
    if not auth:
        headers["Authorization"] = f"Bearer {token}"

    inicio = time.perf_counter()
    permisos_verificados: list[str] = []

    try:
        async with crear_cliente_http(base_url=base_url, headers=headers, auth=auth) as cliente:
            r_me = await cliente.get("/rest/api/3/myself")
            if r_me.status_code in (401, 403):
                return ResultadoPrueba(
                    ok=False,
                    mensaje="Jira rechazó las credenciales (401/403): email o API token inválido o revocado.",
                    latencia_ms=medir_ms(inicio),
                )
            if r_me.status_code >= 500:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=f"Jira devolvió un error de servidor ({r_me.status_code}).",
                    latencia_ms=medir_ms(inicio),
                    proveedor_caido=True,
                )
            r_me.raise_for_status()

            params: dict[str, str] = {
                "permissions": "BROWSE_PROJECTS,CREATE_ISSUES,ADMINISTER",
            }
            if config.get("proyecto") or config.get("project_key"):
                params["projectKey"] = str(config.get("proyecto") or config.get("project_key"))

            r_perms = await cliente.get("/rest/api/3/mypermissions", params=params)
            if r_perms.status_code >= 500:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=f"Jira devolvió {r_perms.status_code} al consultar permisos.",
                    latencia_ms=medir_ms(inicio),
                    proveedor_caido=True,
                )
            r_perms.raise_for_status()

            perms_map = (r_perms.json() or {}).get("permissions") or {}
            tiene_browse = bool((perms_map.get("BROWSE_PROJECTS") or {}).get("havePermission"))
            if not tiene_browse:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=(
                        "Permiso faltante en Jira: el usuario no tiene 'BROWSE_PROJECTS' para leer incidencias. "
                        "Concede el permiso 'Browse Projects' en Jira -> Project Settings -> Permissions."
                    ),
                    permisos=permisos_verificados,
                    latencia_ms=medir_ms(inicio),
                )
            permisos_verificados.append("read:issues")

            if (perms_map.get("CREATE_ISSUES") or {}).get("havePermission"):
                permisos_verificados.append("write:issues")

            tiene_admin = bool((perms_map.get("ADMINISTER") or {}).get("havePermission"))
            if not tiene_admin:
                # Verificar también el endpoint de webhooks (en Jira Cloud algunos scopes lo habilitan sin ADMINISTER global)
                r_wh = await cliente.get("/rest/api/3/webhook")
                if r_wh.status_code in (401, 403):
                    return ResultadoPrueba(
                        ok=False,
                        mensaje=(
                            "Permiso faltante en Jira: falta autorización para gestionar webhooks ('webhooks:manage'). "
                            "Concede el permiso global 'Administer Jira' o el scope OAuth 'manage:jira-webhook' "
                            "en Atlassian Admin -> System -> Global Permissions."
                        ),
                        permisos=permisos_verificados,
                        latencia_ms=medir_ms(inicio),
                    )
                if r_wh.status_code >= 500:
                    return ResultadoPrueba(
                        ok=False,
                        mensaje=f"Jira devolvió {r_wh.status_code} al verificar gestión de webhooks.",
                        permisos=permisos_verificados,
                        latencia_ms=medir_ms(inicio),
                        proveedor_caido=True,
                    )
                r_wh.raise_for_status()

            permisos_verificados.append("webhooks:manage")

    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        return ResultadoPrueba(
            ok=False,
            mensaje=f"No se pudo conectar con Jira ({type(exc).__name__}). Verifica la URL de la instancia.",
            latencia_ms=medir_ms(inicio),
            proveedor_caido=True,
        )
    except httpx.HTTPStatusError as exc:
        return ResultadoPrueba(
            ok=False,
            mensaje=f"Error HTTP inesperado de Jira ({exc.response.status_code}).",
            latencia_ms=medir_ms(inicio),
            proveedor_caido=exc.response.status_code >= 500,
        )

    return ResultadoPrueba(
        ok=True,
        mensaje="Conexión con Jira verificada (lectura de incidencias y gestión de webhooks).",
        permisos=permisos_verificados,
        latencia_ms=medir_ms(inicio),
    )
