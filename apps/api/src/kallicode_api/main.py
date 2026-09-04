"""Kallicode API — aplicación FastAPI.

Superficies (§2.1 del diseño de endpoints):
    /api/v1            Portal Cliente (JWT de usuario)
    /api/v1/webhooks   Integraciones entrantes (firma HMAC)
    /internal/v1       API interna del pipeline (JWT de servicio, red privada)

Transversales que aplica esta app:
    - trace_id por petición + log central de acceso (§3.5)
    - sobre de error estándar para AppError, 422 y 500 (§3.6)
    - CORS para el portal
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kallicode_core.config import get_settings
from kallicode_core.errors import AppError
from kallicode_core.logging import Cronometro, bind_contexto, configurar_logging, limpiar_contexto, log

from .routers import (audit, auth, connections, dashboard, gates, health, internal,
                      models_ia, notifications, organization, plans, tickets, usage,
                      users, webhooks)


def _sobre_error(codigo: str, mensaje: str, detalle=None, trace_id: str = "") -> dict:
    return {"error": {"codigo": codigo, "mensaje": mensaje,
                      **({"detalle": detalle} if detalle else {}),
                      "trace_id": trace_id,
                      "timestamp": datetime.now(timezone.utc).isoformat()}}


def crear_app() -> FastAPI:
    configurar_logging()
    s = get_settings()
    app = FastAPI(title="Kallicode API", version="1.0.0",
                  description="Backend del Portal Cliente, webhooks e interna del pipeline")

    app.add_middleware(CORSMiddleware,
                       allow_origins=[o.strip() for o in s.cors_origins.split(",")],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    # ---------------- middleware: trace + log central de acceso ----------------
    @app.middleware("http")
    async def trazabilidad(request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or f"tr_{uuid.uuid4().hex[:20]}"
        limpiar_contexto()
        bind_contexto(trace_id=trace_id)
        with Cronometro() as c:
            try:
                respuesta = await call_next(request)
            except Exception:
                log.error("http.error_no_controlado", metodo=request.method,
                          ruta=request.url.path, exc_info=True)
                respuesta = JSONResponse(status_code=500, content=_sobre_error(
                    "ERROR_INTERNO", "Error interno; contacta soporte con el trace_id.",
                    trace_id=trace_id))
        respuesta.headers["X-Trace-Id"] = trace_id
        u = getattr(request.state, "usuario", None)
        # Log de acceso estándar (§3.5): una línea INFO por petición.
        if not request.url.path.startswith("/health"):
            log.info("http.acceso", metodo=request.method, ruta=request.url.path,
                     status=respuesta.status_code, latencia_ms=c.ms,
                     tenant_id=getattr(u, "tenant_id", None),
                     user_id=getattr(u, "user_id", None))
        return respuesta

    # ---------------- handlers de error (§3.6) ----------------
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        trace_id = request.headers.get("X-Trace-Id", "")
        nivel = log.warning if exc.http < 500 else log.error
        nivel("http.app_error", codigo=exc.codigo, http=exc.http, ruta=request.url.path)
        headers = {}
        if exc.codigo == "RATE_LIMIT_EXCEDIDO" and exc.detalle:
            headers["Retry-After"] = str(exc.detalle.get("retry_after_s", 60))
        return JSONResponse(status_code=exc.http, headers=headers,
                            content=_sobre_error(exc.codigo, exc.mensaje, exc.detalle, trace_id))

    @app.exception_handler(RequestValidationError)
    async def _validacion(request: Request, exc: RequestValidationError):
        detalle = [{"campo": ".".join(str(x) for x in e["loc"][1:]), "error": e["msg"]}
                   for e in exc.errors()]
        return JSONResponse(status_code=422, content=_sobre_error(
            "VALIDACION_ENTRADA", "Revisa los campos marcados.", {"campos": detalle}))

    # ---------------- routers ----------------
    app.include_router(health.router)
    for r in (auth, users, organization, dashboard, tickets, gates, audit,
              notifications, connections, models_ia, usage, plans):
        app.include_router(r.router, prefix="/api/v1")
    app.include_router(webhooks.router, prefix="/api/v1/webhooks")
    app.include_router(internal.router, prefix="/internal/v1")
    return app


app = crear_app()
