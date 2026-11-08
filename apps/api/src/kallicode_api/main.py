"""Aplicaciones FastAPI de Kallicode.

La aplicación pública registra el portal y los webhooks.
La aplicación interna se construye con crear_app(interna=True)
y se sirve mediante main_interna.py en otro listener.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Request
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
    models_ia,
    notifications,
    organization,
    plans,
    tickets,
    usage,
    users,
    webhooks,
)


def _sobre_error(
    codigo: str,
    mensaje: str,
    detalle=None,
    trace_id: str = "",
) -> dict:
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
    """Inicializa y cierra los recursos del proceso."""
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


def crear_app(*, interna: bool = False) -> FastAPI:
    configurar_logging()
    settings = get_settings()

    app = FastAPI(
        title="Kallicode API interna" if interna else "Kallicode API",
        version="1.0.0",
        description=("API interna del pipeline" if interna else "Portal Cliente y webhooks"),
        docs_url=None if interna else "/docs",
        redoc_url=None if interna else "/redoc",
        openapi_url=None if interna else "/openapi.json",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def trazabilidad(request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or f"tr_{uuid.uuid4().hex[:20]}"
        request.state.trace_id = trace_id

        limpiar_contexto()
        bind_contexto(trace_id=trace_id)

        with Cronometro() as cronometro:
            try:
                respuesta = await call_next(request)
            except Exception:
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
                        "Error interno; contacta soporte con el trace_id.",
                        trace_id=trace_id,
                    ),
                )

        respuesta.headers["X-Trace-Id"] = trace_id
        usuario = getattr(request.state, "usuario", None)

        if not request.url.path.startswith("/health"):
            log.info(
                "http.acceso",
                metodo=request.method,
                ruta=request.url.path,
                status=respuesta.status_code,
                latencia_ms=cronometro.ms,
                tenant_id=getattr(usuario, "tenant_id", None),
                user_id=getattr(usuario, "user_id", None),
            )

        return respuesta

    app.add_middleware(
        LimiteCuerpoMiddleware,
        max_bytes=settings.max_body_bytes,
    )

    if not interna:
        origenes = validar_cors(
            settings.cors_origins.split(","),
            allow_credentials=True,
        )
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origenes,
            allow_credentials=True,
            allow_methods=[
                "GET",
                "POST",
                "PATCH",
                "PUT",
                "DELETE",
                "OPTIONS",
            ],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-Trace-Id",
            ],
            expose_headers=[
                "X-Trace-Id",
                "Retry-After",
            ],
        )

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[host.strip() for host in settings.allowed_hosts.split(",") if host.strip()],
    )
    app.add_middleware(CabecerasSeguridadMiddleware)

    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        trace_id = getattr(request.state, "trace_id", "")
        nivel = log.warning if exc.http < 500 else log.error

        nivel(
            "http.app_error",
            codigo=exc.codigo,
            http=exc.http,
            ruta=request.url.path,
        )

        headers = {}
        if exc.codigo == "RATE_LIMIT_EXCEDIDO" and exc.detalle:
            headers["Retry-After"] = str(exc.detalle.get("retry_after_s", 60))

        return JSONResponse(
            status_code=exc.http,
            headers=headers,
            content=_sobre_error(
                exc.codigo,
                exc.mensaje,
                exc.detalle,
                trace_id,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def _validacion(
        request: Request,
        exc: RequestValidationError,
    ):
        trace_id = getattr(request.state, "trace_id", "")
        detalle = [
            {
                "campo": ".".join(str(parte) for parte in error["loc"][1:]),
                "error": error["msg"],
            }
            for error in exc.errors()
        ]

        return JSONResponse(
            status_code=422,
            content=_sobre_error(
                "VALIDACION_ENTRADA",
                "Revisa los campos marcados.",
                {"campos": detalle},
                trace_id,
            ),
        )

    app.include_router(health.router)

    if interna:
        from .deps import servicio_actual
        from .routers import internal

        # Protege todas las operaciones del router, incluidas las futuras.
        app.include_router(
            internal.router,
            prefix="/internal/v1",
            dependencies=[Depends(servicio_actual)],
        )
        return app

    for modulo in (
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
        app.include_router(
            modulo.router,
            prefix="/api/v1",
        )

    app.include_router(
        webhooks.router,
        prefix="/api/v1/webhooks",
    )

    return app


app = crear_app()
