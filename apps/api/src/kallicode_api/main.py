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
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from kallicode_core import comercial
from kallicode_core import db as db_module
from kallicode_core.config import get_settings
from kallicode_core.errors import AppError
from kallicode_core.logging import (
    Cronometro,
    bind_contexto,
    configurar_logging,
    limpiar_contexto,
    log,
)

from .middleware import (
    CabecerasSeguridadMiddleware,
    LimiteCuerpoMiddleware,
    validar_cors,
)
from .routers import (
    audit,
    auth,
    connections,
    curation,
    dashboard,
    gates,
    health,
    internal,
    models_ia,
    notifications,
    organization,
    plans,
    tickets,
    usage,
    users,
    webhooks,
)


def _sobre_error(codigo: str, mensaje: str, detalle=None, trace_id: str = "") -> dict:
    return {
        "error": {
            "codigo": codigo,
            "mensaje": mensaje,
            **({"detalle": detalle} if detalle else {}),
            "trace_id": trace_id,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Crea engines y cliente Redis al arrancar; los cierra al parar (BE-F37)."""
    await db_module._init_engines()
    comercial.redis_cliente()
    try:
        yield
    finally:
        if db_module._engine_app is not None:
            await db_module._engine_app.dispose()
        if db_module._engine_admin is not None:
            await db_module._engine_admin.dispose()
        if comercial._redis is not None:
            await comercial._redis.aclose()
        db_module._engine_app = None
        db_module._engine_admin = None
        db_module._sessionmaker_app = None
        db_module._sessionmaker_admin = None
        comercial._redis = None


def crear_app() -> FastAPI:
    configurar_logging()
    s = get_settings()
    app = FastAPI(
        title="Kallicode API",
        version="1.0.0",
        description="Backend del Portal Cliente, webhooks e interna del pipeline",
        lifespan=lifespan,
    )

    # ---------------- middleware: trace + log central de acceso ----------------
    @app.middleware("http")
    async def trazabilidad(request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or f"tr_{uuid.uuid4().hex[:20]}"
        # BE-F26: Guardar el trace_id en el estado de la request
        request.state.trace_id = trace_id

        limpiar_contexto()
        bind_contexto(trace_id=trace_id)
        with Cronometro() as c:
            try:
                respuesta = await call_next(request)
            except Exception as e:
                import traceback
                log.error(
                    "http.error_no_controlado",
                    metodo=request.method,
                    ruta=request.url.path,
                    exc_info=True,
                )
                respuesta = JSONResponse(
                    status_code=500,
                    content=_sobre_error(
                        "ERROR_INTERNO",
                        f"Error: {type(e).__name__}: {str(e)}",
                        detalle={"traceback": traceback.format_exc()},
                        trace_id=trace_id,
                    ),
                )
        respuesta.headers["X-Trace-Id"] = trace_id
        u = getattr(request.state, "usuario", None)
        # Log de acceso estándar (§3.5): una línea INFO por petición.
        if not request.url.path.startswith("/health"):
            log.info(
                "http.acceso",
                metodo=request.method,
                ruta=request.url.path,
                status=respuesta.status_code,
                latencia_ms=c.ms,
                tenant_id=getattr(u, "tenant_id", None),
                user_id=getattr(u, "user_id", None),
            )
        return respuesta

    # Orden: el ÚLTIMO añadido es el más externo. De fuera hacia dentro:
    # cabeceras → host de confianza → CORS → límite de cuerpo → trazabilidad.
    origenes = validar_cors(s.cors_origins.split(","), allow_credentials=True)
    app.add_middleware(LimiteCuerpoMiddleware, max_bytes=s.max_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origenes,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Trace-Id"],
        expose_headers=["X-Trace-Id", "Retry-After"],
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[h.strip() for h in s.allowed_hosts.split(",") if h.strip()],
    )
    app.add_middleware(CabecerasSeguridadMiddleware)

    # ---------------- handlers de error (§3.6) ----------------
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        # BE-F26: Leer trace_id desde request.state en lugar de headers
        trace_id = getattr(request.state, "trace_id", "")
        nivel = log.warning if exc.http < 500 else log.error
        nivel("http.app_error", codigo=exc.codigo, http=exc.http, ruta=request.url.path)
        headers = {}
        if exc.codigo == "RATE_LIMIT_EXCEDIDO" and exc.detalle:
            headers["Retry-After"] = str(exc.detalle.get("retry_after_s", 60))
        return JSONResponse(
            status_code=exc.http,
            headers=headers,
            content=_sobre_error(exc.codigo, exc.mensaje, exc.detalle, trace_id),
        )

    @app.exception_handler(RequestValidationError)
    async def _validacion(request: Request, exc: RequestValidationError):
        # BE-F26: Leer trace_id desde request.state e inyectarlo en _sobre_error
        trace_id = getattr(request.state, "trace_id", "")
        detalle = [
            {"campo": ".".join(str(x) for x in e["loc"][1:]), "error": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_sobre_error(
                "VALIDACION_ENTRADA", "Revisa los campos marcados.", {"campos": detalle}, trace_id
            ),
        )

    # ---------------- routers ----------------
    app.include_router(health.router)
    for r in (
        auth,
        users,
        organization,
        dashboard,
        tickets,
        gates,
        audit,
        notifications,
        connections,
        curation,
        models_ia,
        usage,
        plans,
    ):
        app.include_router(r.router, prefix="/api/v1")
    app.include_router(webhooks.router, prefix="/api/v1/webhooks")
    app.include_router(internal.router, prefix="/internal/v1")
    return app


app = crear_app()
