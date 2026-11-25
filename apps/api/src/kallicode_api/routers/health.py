"""Salud del servicio: /health (liveness) y /health/ready (dependencias)."""

from __future__ import annotations

import asyncio
import os
import secrets
from time import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from kallicode_core import comercial
from kallicode_core.cifrado import cifrar, descifrar
from kallicode_core.config import get_settings
from kallicode_core.db import sesion_sistema, valor
from kallicode_core.logging import log
from kallicode_core.telemetria import CLAVE_ORQUESTADOR, ORQUESTADOR_TTL_S

try:
    from azure.core.exceptions import ResourceNotFoundError
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
except ImportError:
    ResourceNotFoundError = Exception  # type: ignore[misc,assignment]
    DefaultAzureCredential = None  # type: ignore[assignment]
    SecretClient = None  # type: ignore[assignment]

router = APIRouter(tags=["Salud"])

TIMEOUT_SONDA_POR_DEFECTO_S = 2.0


def _timeout_sonda() -> float:
    return float(getattr(get_settings(), "health_timeout_s", TIMEOUT_SONDA_POR_DEFECTO_S))


def _resumen_error(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError | asyncio.TimeoutError):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "conexion_rechazada"
    if isinstance(exc, OSError):
        return "error_red"
    return type(exc).__name__


async def comprobar_postgres(timeout_s: float | None = None) -> dict[str, Any]:
    limite = timeout_s if timeout_s is not None else _timeout_sonda()
    try:
        async with asyncio.timeout(limite):
            async with sesion_sistema() as db:
                ok = (await valor(db, "SELECT 1")) == 1
        return {"ok": ok, "detalle": "ok" if ok else "respuesta_inesperada"}
    except Exception as exc:
        log.debug("health.postgres_unhealthy", error=str(exc))
        return {"ok": False, "detalle": _resumen_error(exc)}


async def comprobar_redis(timeout_s: float | None = None) -> dict[str, Any]:
    limite = timeout_s if timeout_s is not None else _timeout_sonda()
    try:
        async with asyncio.timeout(limite):
            ok = bool(await comercial.redis_cliente().ping())
        return {"ok": ok, "detalle": "ok" if ok else "sin_pong"}
    except Exception as exc:
        log.debug("health.redis_unhealthy", error=str(exc))
        return {"ok": False, "detalle": _resumen_error(exc)}


def _verificar_keyvault_sync(kv_url: str) -> None:
    if DefaultAzureCredential is None or SecretClient is None:
        raise RuntimeError("sdk_no_instalado")
    cliente = SecretClient(
        vault_url=kv_url,
        credential=DefaultAzureCredential(),
        retry_total=0,
    )
    try:
        next(iter(cliente.list_properties_of_secrets(max_page_size=1)), None)
    except ResourceNotFoundError:
        pass


async def comprobar_boveda(timeout_s: float | None = None) -> dict[str, Any]:
    limite = timeout_s if timeout_s is not None else _timeout_sonda()
    settings = get_settings()
    entorno = str(getattr(settings, "env", os.getenv("KC_ENV", "local"))).lower()
    try:
        async with asyncio.timeout(limite):
            if entorno == "local":
                clave = getattr(settings, "boveda_clave_local", None) or os.getenv(
                    "KC_BOVEDA_CLAVE_LOCAL"
                )
                clave_prueba = clave or secrets.token_hex(32)
                token = cifrar("probe", clave_prueba)
                if descifrar(token, clave_prueba) != "probe":
                    return {"ok": False, "detalle": "cifrado_invalido"}
                ok_redis = bool(await comercial.redis_cliente().ping())
                return {"ok": ok_redis, "detalle": "ok" if ok_redis else "redis_no_disponible"}

            kv_url = getattr(settings, "keyvault_url", None) or os.getenv("KC_KEYVAULT_URL")
            if not kv_url:
                return {"ok": False, "detalle": "keyvault_url_no_configurada"}
            await asyncio.to_thread(_verificar_keyvault_sync, kv_url)
            return {"ok": True, "detalle": "ok"}
    except Exception as exc:
        log.debug("health.boveda_unhealthy", error=str(exc))
        return {"ok": False, "detalle": _resumen_error(exc)}


async def comprobar_orquestador(timeout_s: float | None = None) -> dict[str, Any]:
    limite = timeout_s if timeout_s is not None else _timeout_sonda()
    try:
        from kallicode_workers import salud as salud_workers

        if salud_workers.orquestador_local_detenido():
            return {"ok": False, "detalle": "tarea_detenida"}
    except ImportError:
        pass

    try:
        async with asyncio.timeout(limite):
            ahora = time()
            activos = await comercial.redis_cliente().zcount(
                CLAVE_ORQUESTADOR,
                ahora - ORQUESTADOR_TTL_S,
                "+inf",
            )
        if int(activos) >= 1:
            return {"ok": True, "detalle": "ok"}
        return {"ok": False, "detalle": "sin_pulso_vigente"}
    except Exception as exc:
        log.debug("health.orquestador_unhealthy", error=str(exc))
        return {"ok": False, "detalle": _resumen_error(exc)}


async def evaluar_readiness(
    *,
    incluir_orquestador: bool = False,
    timeout_s: float | None = None,
) -> tuple[int, dict[str, Any]]:
    nombres = ["postgres", "redis", "boveda"]
    tareas = [
        comprobar_postgres(timeout_s),
        comprobar_redis(timeout_s),
        comprobar_boveda(timeout_s),
    ]
    if incluir_orquestador:
        nombres.append("orquestador")
        tareas.append(comprobar_orquestador(timeout_s))

    resultados = await asyncio.gather(*tareas)
    detalle = dict(zip(nombres, resultados, strict=True))
    sano = all(item["ok"] for item in detalle.values())
    cuerpo: dict[str, Any] = {
        "estado": "ok" if sano else "degradado",
        **{nombre: item["ok"] for nombre, item in detalle.items()},
        "detalle": detalle,
    }
    return (200 if sano else 503), cuerpo


def _es_contenedor_workers(request: Request) -> bool:
    if bool(getattr(request.app.state, "incluir_orquestador_en_salud", False)):
        return True
    return os.getenv("KC_WORKERS_CONTAINER", "").lower() in {"1", "true", "yes"}


@router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    return {"estado": "ok", "version": "1.0.1"}


@router.get(
    "/health/ready",
    summary="Readiness: postgres, redis y bóveda",
    responses={
        200: {"description": "Servicio listo para recibir tráfico."},
        503: {"description": "Una o más dependencias críticas no están disponibles."},
    },
)
async def ready(request: Request) -> JSONResponse:
    """Comprueba dependencias críticas con tiempo acotado y devuelve 503 si alguna falla."""
    codigo, cuerpo = await evaluar_readiness(
        incluir_orquestador=_es_contenedor_workers(request),
    )
    return JSONResponse(status_code=codigo, content=cuerpo)
