"""Verificación real de conexión y permisos contra GitHub REST API."""

from __future__ import annotations

import time

import httpx

from ._base import ResultadoPrueba, crear_cliente_http, medir_ms

_API_DEFAULT = "https://api.github.com"


def _extraer_repo(config: dict) -> str | None:
    if config.get("repo") and "/" in str(config["repo"]):
        return str(config["repo"]).strip("/")
    if config.get("owner") and config.get("repo"):
        return f"{str(config['owner']).strip('/')}/{str(config['repo']).strip('/')}"
    if config.get("repositorio"):
        return str(config["repositorio"]).strip("/")
    return None


async def probar_github(config: dict, credenciales: dict) -> ResultadoPrueba:
    token = str(credenciales.get("token") or credenciales.get("pat") or "").strip()
    if not token:
        return ResultadoPrueba(
            ok=False,
            mensaje="Falta el token de acceso personal (PAT) de GitHub en las credenciales.",
        )

    repo = _extraer_repo(config)
    if not repo:
        return ResultadoPrueba(
            ok=False,
            mensaje="Falta el repositorio objetivo en la configuración (formato 'owner/repo').",
        )

    base_url = str(config.get("api_url") or _API_DEFAULT).rstrip("/")
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "kallicode-verifier/1.0",
    }

    inicio = time.perf_counter()
    permisos_verificados: list[str] = []

    try:
        async with crear_cliente_http(base_url=base_url, headers=headers) as cliente:
            r_user = await cliente.get("/user")
            if r_user.status_code == 401:
                return ResultadoPrueba(
                    ok=False,
                    mensaje="GitHub rechazó el token (401 Unauthorized): credencial inválida o revocada.",
                    latencia_ms=medir_ms(inicio),
                )
            if r_user.status_code >= 500:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=f"GitHub devolvió un error de servidor ({r_user.status_code}). Intenta nuevamente.",
                    latencia_ms=medir_ms(inicio),
                    proveedor_caido=True,
                )
            r_user.raise_for_status()

            raw_scopes = r_user.headers.get("X-OAuth-Scopes")
            scopes = (
                {s.strip() for s in raw_scopes.split(",") if s.strip()}
                if raw_scopes is not None
                else None
            )

            if scopes is not None and not ({"repo", "public_repo"} & scopes):
                return ResultadoPrueba(
                    ok=False,
                    mensaje=(
                        "Permiso faltante en GitHub: el token no tiene el scope 'repo'. "
                        "Concede el scope 'repo' en GitHub -> Settings -> Developer settings -> "
                        "Personal access tokens."
                    ),
                    permisos=permisos_verificados,
                    latencia_ms=medir_ms(inicio),
                )

            r_repo = await cliente.get(f"/repos/{repo}")
            if r_repo.status_code == 404:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=(
                        f"No se encontró el repositorio '{repo}' o el token no tiene acceso de lectura. "
                        "Verifica el nombre 'owner/repo' y concede acceso al repositorio en el token."
                    ),
                    permisos=permisos_verificados,
                    latencia_ms=medir_ms(inicio),
                )
            if r_repo.status_code == 403:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=(
                        f"Acceso denegado (403) al repositorio '{repo}'. "
                        "Concede permisos de lectura y escritura (Contents: Read & Write) sobre el repositorio."
                    ),
                    permisos=permisos_verificados,
                    latencia_ms=medir_ms(inicio),
                )
            if r_repo.status_code >= 500:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=f"GitHub devolvió {r_repo.status_code} al consultar '{repo}'.",
                    latencia_ms=medir_ms(inicio),
                    proveedor_caido=True,
                )
            r_repo.raise_for_status()

            repo_data = r_repo.json()
            perms = repo_data.get("permissions") or {}
            if perms.get("pull", True):
                permisos_verificados.append("repo:read")

            if not perms.get("push"):
                return ResultadoPrueba(
                    ok=False,
                    mensaje=(
                        f"Permiso insuficiente en '{repo}': el token solo tiene lectura, pero falta "
                        "permiso de escritura ('push') para crear ramas y pull requests. "
                        "Concede rol Write en el repositorio o permiso 'Contents: Read and write' y "
                        "'Pull requests: Read and write' en el token."
                    ),
                    permisos=permisos_verificados,
                    latencia_ms=medir_ms(inicio),
                )

            permisos_verificados.append("repo:write")
            permisos_verificados.append("pr:write")

            if scopes is not None and "admin:repo_hook" not in scopes and "repo" not in scopes:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=(
                        "Permiso faltante en GitHub: falta gestión de webhooks ('admin:repo_hook'). "
                        "Activa 'admin:repo_hook' en los scopes del token."
                    ),
                    permisos=permisos_verificados,
                    latencia_ms=medir_ms(inicio),
                )

            if perms.get("admin") or (
                scopes is not None and ({"admin:repo_hook", "repo"} & scopes)
            ):
                permisos_verificados.append("webhooks:manage")

    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        return ResultadoPrueba(
            ok=False,
            mensaje=f"No se pudo conectar con GitHub ({type(exc).__name__}). Verifica la conectividad.",
            latencia_ms=medir_ms(inicio),
            proveedor_caido=True,
        )
    except httpx.HTTPStatusError as exc:
        return ResultadoPrueba(
            ok=False,
            mensaje=f"Error HTTP inesperado de GitHub ({exc.response.status_code}).",
            latencia_ms=medir_ms(inicio),
            proveedor_caido=exc.response.status_code >= 500,
        )

    return ResultadoPrueba(
        ok=True,
        mensaje=f"Conexión con GitHub verificada sobre '{repo}'.",
        permisos=permisos_verificados,
        latencia_ms=medir_ms(inicio),
    )
