"""Verificación real de conexión y permisos contra GitLab REST API v4."""

from __future__ import annotations

import time
from urllib.parse import quote

import httpx

from ._base import ResultadoPrueba, crear_cliente_http, medir_ms

_API_DEFAULT = "https://gitlab.com/api/v4"


def _extraer_proyecto(config: dict) -> str | None:
    for clave in ("proyecto", "project", "repo", "repositorio"):
        if config.get(clave):
            return str(config[clave]).strip("/")
    return None


async def probar_gitlab(config: dict, credenciales: dict) -> ResultadoPrueba:
    token = str(credenciales.get("token") or credenciales.get("pat") or "").strip()
    if not token:
        return ResultadoPrueba(
            ok=False,
            mensaje="Falta el token de acceso personal (PAT) de GitLab en las credenciales.",
        )

    proyecto = _extraer_proyecto(config)
    if not proyecto:
        return ResultadoPrueba(
            ok=False,
            mensaje="Falta el proyecto de GitLab en la configuración (formato 'grupo/proyecto').",
        )

    base_url = str(config.get("api_url") or _API_DEFAULT).rstrip("/")
    headers = {
        "PRIVATE-TOKEN": token,
        "Accept": "application/json",
        "User-Agent": "kallicode-verifier/1.0",
    }

    inicio = time.perf_counter()
    permisos_verificados: list[str] = []

    try:
        async with crear_cliente_http(base_url=base_url, headers=headers) as cliente:
            r_pat = await cliente.get("/personal_access_tokens/self")
            if r_pat.status_code == 401:
                return ResultadoPrueba(
                    ok=False,
                    mensaje="GitLab rechazó el token (401 Unauthorized): credencial inválida o revocada.",
                    latencia_ms=medir_ms(inicio),
                )
            if r_pat.status_code >= 500:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=f"GitLab devolvió un error de servidor ({r_pat.status_code}).",
                    latencia_ms=medir_ms(inicio),
                    proveedor_caido=True,
                )

            scopes: set[str] | None = None
            if r_pat.status_code == 200:
                pat_data = r_pat.json()
                if pat_data.get("revoked"):
                    return ResultadoPrueba(
                        ok=False,
                        mensaje="El token de GitLab está revocado.",
                        latencia_ms=medir_ms(inicio),
                    )
                scopes = set(pat_data.get("scopes") or [])
                if not ({"api", "write_repository"} & scopes):
                    return ResultadoPrueba(
                        ok=False,
                        mensaje=(
                            "Permiso faltante en GitLab: el token no tiene el scope 'api' ni "
                            "'write_repository'. Genera o edita el token en GitLab -> User Settings -> "
                            "Access Tokens marcando el scope 'api' (o 'write_repository' + 'read_api')."
                        ),
                        permisos=list(scopes),
                        latencia_ms=medir_ms(inicio),
                    )
                if "read_repository" in scopes or "api" in scopes:
                    permisos_verificados.append("read_repository")

            proyecto_cod = quote(proyecto, safe="")
            r_proj = await cliente.get(f"/projects/{proyecto_cod}")
            if r_proj.status_code == 401:
                return ResultadoPrueba(
                    ok=False,
                    mensaje="GitLab rechazó el token (401 Unauthorized): credencial inválida o revocada.",
                    latencia_ms=medir_ms(inicio),
                )
            if r_proj.status_code == 404:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=(
                        f"No se encontró el proyecto '{proyecto}' en GitLab o el usuario no tiene acceso. "
                        "Verifica la ruta 'grupo/proyecto' y añade el usuario como Developer o Maintainer."
                    ),
                    permisos=permisos_verificados,
                    latencia_ms=medir_ms(inicio),
                )
            if r_proj.status_code == 403:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=(
                        f"Acceso denegado (403) al proyecto '{proyecto}'. "
                        "Concede rol Developer (access_level >= 30) en Members del proyecto."
                    ),
                    permisos=permisos_verificados,
                    latencia_ms=medir_ms(inicio),
                )
            if r_proj.status_code >= 500:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=f"GitLab devolvió {r_proj.status_code} al consultar '{proyecto}'.",
                    latencia_ms=medir_ms(inicio),
                    proveedor_caido=True,
                )
            r_proj.raise_for_status()

            proj_data = r_proj.json()
            perms = proj_data.get("permissions") or {}
            proj_access = (perms.get("project_access") or {}).get("access_level", 0)
            group_access = (perms.get("group_access") or {}).get("access_level", 0)
            nivel = max(proj_access, group_access)

            if "read_repository" not in permisos_verificados:
                permisos_verificados.append("read_repository")

            # En GitLab: 10=Guest, 20=Reporter (solo lectura), 30=Developer (escritura), 40=Maintainer
            if nivel < 30:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=(
                        f"Permiso insuficiente en el proyecto '{proyecto}' (nivel actual: {nivel}). "
                        "Se requiere rol Developer (30) o Maintainer (40) para crear ramas y merge requests. "
                        "Concede rol Developer en Project -> Manage -> Members."
                    ),
                    permisos=permisos_verificados,
                    latencia_ms=medir_ms(inicio),
                )

            permisos_verificados.append("write_repository")
            if scopes is None or "api" in scopes:
                permisos_verificados.append("merge_requests:write")

    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        return ResultadoPrueba(
            ok=False,
            mensaje=f"No se pudo conectar con GitLab ({type(exc).__name__}). Verifica la URL o conectividad.",
            latencia_ms=medir_ms(inicio),
            proveedor_caido=True,
        )
    except httpx.HTTPStatusError as exc:
        return ResultadoPrueba(
            ok=False,
            mensaje=f"Error HTTP inesperado de GitLab ({exc.response.status_code}).",
            latencia_ms=medir_ms(inicio),
            proveedor_caido=exc.response.status_code >= 500,
        )

    return ResultadoPrueba(
        ok=True,
        mensaje=f"Conexión con GitLab verificada sobre '{proyecto}'.",
        permisos=permisos_verificados,
        latencia_ms=medir_ms(inicio),
    )
