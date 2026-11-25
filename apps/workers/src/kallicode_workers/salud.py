"""Sondas de salud y readiness del contenedor de workers (KC-BE-10-4)."""

from __future__ import annotations

import asyncio
import os
import secrets
from time import time
from typing import Any

import uvicorn
from fastapi import FastAPI
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

TIMEOUT_SONDA_POR_DEFECTO_S = 2.0
PUERTO_SALUD_POR_DEFECTO = 8081

_tarea_orquestador: asyncio.Task[Any] | None = None


def registrar_orquestador(tarea: asyncio.Task[Any] | None) -> None:
    """Registra la tarea principal del orquestador en el proceso de workers."""
    global _tarea_orquestador
    _tarea_orquestador = tarea


def orquestador_local_detenido() -> bool:
    """Indica si la tarea del orquestador en este proceso terminó inesperadamente."""
    return _tarea_orquestador is not None and _tarea_orquestador.done()


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
        log.debug("workers.health.postgres_unhealthy", error=str(exc))
        return {"ok": False, "detalle": _resumen_error(exc)}


async def comprobar_redis(timeout_s: float | None = None) -> dict[str, Any]:
    limite = timeout_s if timeout_s is not None else _timeout_sonda()
    try:
        async with asyncio.timeout(limite):
            ok = bool(await comercial.redis_cliente().ping())
        return {"ok": ok, "detalle": "ok" if ok else "sin_pong"}
    except Exception as exc:
        log.debug("workers.health.redis_unhealthy", error=str(exc))
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
        log.debug("workers.health.boveda_unhealthy", error=str(exc))
        return {"ok": False, "detalle": _resumen_error(exc)}


async def comprobar_orquestador(timeout_s: float | None = None) -> dict[str, Any]:
    limite = timeout_s if timeout_s is not None else _timeout_sonda()
    if orquestador_local_detenido():
        return {"ok": False, "detalle": "tarea_detenida"}

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
        log.debug("workers.health.orquestador_unhealthy", error=str(exc))
        return {"ok": False, "detalle": _resumen_error(exc)}


async def evaluar_readiness(timeout_s: float | None = None) -> tuple[int, dict[str, Any]]:
    """Evalúa PostgreSQL, Redis, bóveda y orquestador con tiempo acotado."""
    nombres = ("postgres", "redis", "boveda", "orquestador")
    resultados = await asyncio.gather(
        comprobar_postgres(timeout_s),
        comprobar_redis(timeout_s),
        comprobar_boveda(timeout_s),
        comprobar_orquestador(timeout_s),
    )
    detalle = dict(zip(nombres, resultados, strict=True))
    sano = all(item["ok"] for item in detalle.values())
    cuerpo: dict[str, Any] = {
        "estado": "ok" if sano else "degradado",
        **{nombre: item["ok"] for nombre, item in detalle.items()},
        "detalle": detalle,
    }
    return (200 if sano else 503), cuerpo


def crear_app_salud() -> FastAPI:
    """Aplicación ASGI ligera para sondas /health y /health/ready de workers."""
    app_salud = FastAPI(title="Kallicode Workers Salud", docs_url=None, redoc_url=None)

    @app_salud.get("/health", summary="Liveness de workers")
    async def health() -> dict[str, str]:
        return {"estado": "ok", "version": "1.0.1"}

    @app_salud.get("/health/ready", summary="Readiness de workers")
    async def ready() -> JSONResponse:
        codigo, cuerpo = await evaluar_readiness()
        return JSONResponse(status_code=codigo, content=cuerpo)

    return app_salud


async def servir_salud(
    parar: asyncio.Event,
    host: str = "0.0.0.0",
    port: int | None = None,
) -> None:
    """Levanta el listener HTTP de salud para workers hasta que `parar` se active."""
    puerto_raw = os.getenv("KC_WORKERS_HEALTH_PORT", str(PUERTO_SALUD_POR_DEFECTO))
    puerto = port if port is not None else int(puerto_raw)
    if puerto <= 0:
        return

    config = uvicorn.Config(
        crear_app_salud(),
        host=host,
        port=puerto,
        log_level="warning",
        access_log=False,
    )
    servidor = uvicorn.Server(config)
    servidor.install_signal_handlers = lambda: None  # type: ignore[method-assign]

    tarea_srv = asyncio.create_task(servidor.serve())
    espera_parar = asyncio.create_task(parar.wait())
    try:
        _, pendientes = await asyncio.wait(
            {tarea_srv, espera_parar},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if espera_parar not in pendientes:
            servidor.should_exit = True
            await tarea_srv
    finally:
        espera_parar.cancel()
        if not tarea_srv.done():
            servidor.should_exit = True
            await tarea_srv
