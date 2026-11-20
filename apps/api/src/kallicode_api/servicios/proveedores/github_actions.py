"""Verificación real de conexión y permisos contra GitHub Actions API."""

from __future__ import annotations

import time

import httpx

from ._base import ResultadoPrueba, crear_cliente_http, medir_ms
from .github import _api_base, _extraer_repo


async def probar_github_actions(config: dict, credenciales: dict) -> ResultadoPrueba:
    token = str(credenciales.get("token") or credenciales.get("pat") or "").strip()
    if not token:
        return ResultadoPrueba(
            ok=False,
            mensaje="Falta el token de GitHub en las credenciales para verificar GitHub Actions.",
        )

    repo = _extraer_repo(config)
    if not repo:
        return ResultadoPrueba(
            ok=False,
            mensaje=(
                "Falta el repositorio objetivo en la configuración "
                "(URL del repositorio o formato 'owner/repo')."
            ),
        )

    base_url = _api_base(config)
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
            r_repo = await cliente.get(f"/repos/{repo}")
            if r_repo.status_code == 401:
                return ResultadoPrueba(
                    ok=False,
                    mensaje="GitHub rechazó el token (401 Unauthorized): credencial inválida o revocada.",
                    latencia_ms=medir_ms(inicio),
                )
            if r_repo.status_code == 404:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=(
                        f"No se encontró el repositorio '{repo}' o el token no tiene acceso. "
                        "Concede acceso al repositorio en GitHub Settings -> Personal access tokens."
                    ),
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

            raw_scopes = r_repo.headers.get("X-OAuth-Scopes")
            scopes = (
                {s.strip() for s in raw_scopes.split(",") if s.strip()}
                if raw_scopes is not None
                else None
            )
            perms = (r_repo.json() or {}).get("permissions") or {}

            r_wf = await cliente.get(f"/repos/{repo}/actions/workflows")
            if r_wf.status_code in (401, 403):
                return ResultadoPrueba(
                    ok=False,
                    mensaje=(
                        f"Permiso faltante en GitHub Actions: no se pueden listar los workflows de '{repo}'. "
                        "Concede el permiso 'Actions: Read and write' (o el scope 'workflow' en PAT clásico)."
                    ),
                    permisos=permisos_verificados,
                    latencia_ms=medir_ms(inicio),
                )
            if r_wf.status_code >= 500:
                return ResultadoPrueba(
                    ok=False,
                    mensaje=f"GitHub Actions devolvió {r_wf.status_code} al listar workflows.",
                    latencia_ms=medir_ms(inicio),
                    proveedor_caido=True,
                )
            r_wf.raise_for_status()

            permisos_verificados.append("workflows:read")

            # Dispatch requiere permiso de escritura en el repo (push) y, en PAT clásico, scope 'workflow'
            falta_scope_wf = scopes is not None and "workflow" not in scopes
            if not perms.get("push") or falta_scope_wf:
                detalle_falta = (
                    "el scope 'workflow' en el PAT"
                    if falta_scope_wf
                    else "permiso de escritura ('push') sobre el repositorio"
                )
                return ResultadoPrueba(
                    ok=False,
                    mensaje=(
                        f"Permiso insuficiente en GitHub Actions para '{repo}': falta {detalle_falta} "
                        "para disparar workflows ('workflows:dispatch'). "
                        "Activa 'workflow' en PAT clásico o 'Actions: Read and write' en Fine-grained PAT."
                    ),
                    permisos=permisos_verificados,
                    latencia_ms=medir_ms(inicio),
                )

            permisos_verificados.append("workflows:dispatch")

    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
        return ResultadoPrueba(
            ok=False,
            mensaje=f"No se pudo conectar con GitHub Actions ({type(exc).__name__}).",
            latencia_ms=medir_ms(inicio),
            proveedor_caido=True,
        )
    except httpx.HTTPStatusError as exc:
        return ResultadoPrueba(
            ok=False,
            mensaje=f"Error HTTP inesperado de GitHub Actions ({exc.response.status_code}).",
            latencia_ms=medir_ms(inicio),
            proveedor_caido=exc.response.status_code >= 500,
        )

    return ResultadoPrueba(
        ok=True,
        mensaje=f"Conexión con GitHub Actions verificada sobre '{repo}'.",
        permisos=permisos_verificados,
        latencia_ms=medir_ms(inicio),
    )
