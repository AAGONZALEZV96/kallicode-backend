"""Frontera HTTP: cabeceras de seguridad y límite global de cuerpo (KC-BE-01-6)."""

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime

from kallicode_core.errors import AppError

CABECERAS_SEGURIDAD: list[tuple[bytes, bytes]] = [
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
]

# Swagger/ReDoc cargan scripts y estilos externos: CSP más laxa solo para esas rutas.
_CSP_DOCS = (
    b"default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    b"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    b"img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
)
_RUTAS_DOCS = ("/docs", "/redoc", "/openapi.json")

# Rutas de subida exentas del límite global (TODO: confirmar con el router real).
RUTAS_SUBIDA = (re.compile(r"^/api/v1/tickets/[^/]+/attachments/?$"),)


def es_subida(ruta: str) -> bool:
    return any(p.match(ruta) for p in RUTAS_SUBIDA)


class CabecerasSeguridadMiddleware:
    """Añade las cabeceras a TODA respuesta HTTP, incluidos 400, 413 y 500."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        es_docs = scope["path"] in _RUTAS_DOCS

        async def send_con_cabeceras(message):
            if message["type"] == "http.response.start":
                actuales = list(message.get("headers", []))
                nombres = {k.lower() for k, _ in actuales}
                for k, v in CABECERAS_SEGURIDAD:
                    if k in nombres:
                        continue
                    if k == b"content-security-policy" and es_docs:
                        v = _CSP_DOCS
                    actuales.append((k, v))
                message = {**message, "headers": actuales}
            await send(message)

        await self.app(scope, receive, send_con_cabeceras)


class LimiteCuerpoMiddleware:
    """413 si el cuerpo supera `max_bytes`, salvo rutas de subida declaradas."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or es_subida(scope["path"]):
            await self.app(scope, receive, send)
            return

        # 1) Content-Length declarado: rechazo inmediato, sin leer nada
        for k, v in scope["headers"]:
            if k == b"content-length":
                try:
                    if int(v) > self.max_bytes:
                        await self._responder_413(send)
                        return
                except ValueError:
                    pass

        # 2) Sin Content-Length (chunked): contar bytes; AppError lo formatea el handler
        recibidos = 0

        async def receive_limitado():
            nonlocal recibidos
            message = await receive()
            if message["type"] == "http.request":
                recibidos += len(message.get("body", b""))
                if recibidos > self.max_bytes:
                    raise AppError(
                        "CUERPO_DEMASIADO_GRANDE",
                        413,
                        "El cuerpo de la petición supera el límite permitido.",
                        {"max_bytes": self.max_bytes},
                    )
            return message

        await self.app(scope, receive_limitado, send)

    async def _responder_413(self, send):
        cuerpo = json.dumps(
            {
                "error": {
                    "codigo": "CUERPO_DEMASIADO_GRANDE",
                    "mensaje": "El cuerpo de la petición supera el límite permitido.",
                    "detalle": {"max_bytes": self.max_bytes},
                    "trace_id": "",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            }
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(cuerpo)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": cuerpo})


def validar_cors(origenes: Iterable[str], allow_credentials: bool) -> list[str]:
    """Aborta el arranque si hay '*' con credenciales (BE-F25)."""
    lista = [o.strip() for o in origenes if o.strip()]
    if allow_credentials and "*" in lista:
        raise RuntimeError(
            "cors_origins no puede contener '*' con allow_credentials=True; "
            "enumera los orígenes del portal explícitamente."
        )
    return lista
