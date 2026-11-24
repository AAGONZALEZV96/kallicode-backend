"""Aplicaciones FastAPI de Kallicode.

La aplicación pública registra el portal y los webhooks.
La aplicación interna se construye con crear_app(interna=True)
y se sirve mediante main_interna.py en otro listener.
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from kallicode_core import comercial, limites
from kallicode_core import db as db_module
from kallicode_core.config import get_settings
from kallicode_core.errors import RETRY_AFTER_DEPENDENCIA_S, AppError, fallo_de_dependencia
from kallicode_core.logging import (
    Cronometro,
    bind_contexto,
    configurar_logging,
    limpiar_contexto,
    log,
)
from sqlalchemy.exc import InterfaceError, OperationalError

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
from .servicios import blob


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
    if get_settings().env != "local":
        await asyncio.to_thread(blob.verificar_delegacion)

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
        limites.iniciar_contexto_request()
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
        es_superficie_api = request.url.path.startswith(("/api/v1", "/internal/v1"))
        cabeceras_rl = limites.cabeceras_contexto_actual(incluir_defecto=es_superficie_api)
        for clave_h, valor_h in cabeceras_rl.items():
            if clave_h not in respuesta.headers:
                respuesta.headers[clave_h] = valor_h
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
            # If-Match y ETag: concurrencia optimista (KC-BE-07-3) desde el portal en otro
            # origen; sin ellos el navegador bloquea el If-Match y no deja leer el ETag (KC-PT-10-4).
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-Trace-Id",
                "If-Match",
            ],
            expose_headers=[
                "ETag",
                "X-Trace-Id",
                "Retry-After",
                "X-RateLimit-Limit",
                "X-RateLimit-Remaining",
                "X-RateLimit-Reset",
                "X-RateLimit-Limit-Usuario",
                "X-RateLimit-Remaining-Usuario",
                "X-RateLimit-Reset-Usuario",
                "X-RateLimit-Limit-Tenant",
                "X-RateLimit-Remaining-Tenant",
                "X-RateLimit-Reset-Tenant",
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
        detalle_publico = dict(exc.detalle) if exc.detalle else None
        if exc.codigo == "RATE_LIMIT_EXCEDIDO":
            retry_after_s = (detalle_publico or {}).get("retry_after_s", 60)
            headers["Retry-After"] = str(retry_after_s)
            rl_headers = (
                detalle_publico.pop("_rate_limit_headers", None) if detalle_publico else None
            ) or limites.cabeceras_contexto_actual(incluir_defecto=True)
            headers.update(rl_headers)
        if exc.codigo == "DEPENDENCIA_NO_DISPONIBLE":
            headers["Retry-After"] = str(RETRY_AFTER_DEPENDENCIA_S)

        return JSONResponse(
            status_code=exc.http,
            headers=headers,
            content=_sobre_error(
                exc.codigo,
                exc.mensaje,
                detalle_publico,
                trace_id,
            ),
        )

    # KC-CM-11-5: una caída de PostgreSQL es un 503 tipificado con Retry-After, no un 500 sin
    # catalogar. asyncpg lanza el ConnectionRefusedError/TimeoutError del sistema sin envolver al
    # conectar; con la conexión ya abierta llega como OperationalError/InterfaceError de SQLAlchemy.
    async def _caida_dependencia(request: Request, exc: Exception):
        error = fallo_de_dependencia(exc)
        if error is None:
            raise exc
        log.error(
            "http.dependencia_caida",
            servicio=(error.detalle or {}).get("servicio"),
            tipo=type(exc).__name__,
            ruta=request.url.path,
        )
        return await _app_error(request, error)

    for tipo_caida in (OperationalError, InterfaceError, ConnectionError, TimeoutError):
        app.add_exception_handler(tipo_caida, _caida_dependencia)

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
        from .routers import codemapping_grafo, internal

        # Protege todas las operaciones del router, incluidas las futuras.
        app.include_router(
            internal.router,
            prefix="/internal/v1",
            dependencies=[Depends(servicio_actual)],
        )
        app.include_router(
            codemapping_grafo.router,
            prefix="/internal/v1",
            dependencies=[Depends(servicio_actual)],
        )
        app.include_router(plans.router_interno, prefix="/internal/v1")
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
