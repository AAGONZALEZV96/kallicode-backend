"""Log central estructurado (§3.5 del diseño de endpoints).

Todas las transacciones — peticiones HTTP, operaciones de negocio, llamadas
LLM, trabajos de workers — se registran en UN log central con formato JSON
por línea hacia stdout, que Azure Monitor / Application Insights recoge
(Loki/Grafana en on-premise). Campos estándar en cada línea:

    timestamp, nivel, evento, trace_id, tenant_id, actor,
    y los campos propios de cada evento (ticket_id, duracion_ms, ...).

Higiene obligatoria: nunca se loguean contraseñas, tokens, API keys ni
cuerpos completos del usuario; los emails se registran como hash.

Uso:
    from kallicode_core.logging import log, bind_contexto
    bind_contexto(trace_id="tr_...", tenant_id="org_x", actor="user:u_1")
    log.info("tickets.creado", ticket_id="KC-1045", tipo="bug")
    log.error("webhooks.normalizacion", delivery_id=d, exc_info=True)
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Any

from .config import get_settings

# Contexto de la transacción actual (lo fija el middleware / el worker).
_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("kc_log_ctx", default={})

_CAMPOS_PROHIBIDOS = {"password", "token", "api_key", "authorization", "secret",
                      "credenciales", "refresh_token", "access_token"}


def bind_contexto(**campos: Any) -> None:
    """Fija campos de contexto (trace_id, tenant_id, actor) para el hilo/tarea."""
    actual = dict(_ctx.get())
    actual.update({k: v for k, v in campos.items() if v is not None})
    _ctx.set(actual)


def limpiar_contexto() -> None:
    _ctx.set({})


def hash_email(email: str) -> str:
    """Los emails se loguean como hash corto (higiene §3.5)."""
    return hashlib.sha256(email.lower().encode()).hexdigest()[:12]


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        linea: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "nivel": record.levelname,
            "evento": record.getMessage(),
        }
        linea.update(_ctx.get())
        extra = getattr(record, "kc_campos", None)
        if extra:
            for k, v in extra.items():
                if k.lower() in _CAMPOS_PROHIBIDOS:
                    linea[k] = "[REDACTADO]"
                else:
                    linea[k] = v
        if record.exc_info and record.exc_info[0]:
            linea["excepcion"] = self.formatException(record.exc_info)
        return json.dumps(linea, ensure_ascii=False, default=str)


class _TextoFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ctx = _ctx.get()
        extra = getattr(record, "kc_campos", {}) or {}
        partes = [f"{k}={v}" for k, v in {**ctx, **extra}.items()]
        base = f"{datetime.now().strftime('%H:%M:%S')} {record.levelname:7} {record.getMessage()}"
        if partes:
            base += "  " + " ".join(partes)
        if record.exc_info and record.exc_info[0]:
            base += "\n" + self.formatException(record.exc_info)
        return base


class _Log:
    """Fachada del log central: log.info(evento, **campos)."""

    def __init__(self) -> None:
        self._logger = logging.getLogger("kallicode")

    def _emitir(self, nivel: int, evento: str, exc_info: bool = False, **campos: Any) -> None:
        self._logger.log(nivel, evento, exc_info=exc_info, extra={"kc_campos": campos})

    def debug(self, evento: str, **c: Any) -> None: self._emitir(logging.DEBUG, evento, **c)
    def info(self, evento: str, **c: Any) -> None: self._emitir(logging.INFO, evento, **c)
    def warning(self, evento: str, **c: Any) -> None: self._emitir(logging.WARNING, evento, **c)

    def error(self, evento: str, exc_info: bool = False, **c: Any) -> None:
        self._emitir(logging.ERROR, evento, exc_info=exc_info, **c)

    def critical(self, evento: str, exc_info: bool = False, **c: Any) -> None:
        self._emitir(logging.CRITICAL, evento, exc_info=exc_info, **c)


log = _Log()


def configurar_logging() -> None:
    """Inicializa el log central. Llamar una vez al arrancar API o worker."""
    s = get_settings()
    raiz = logging.getLogger("kallicode")
    raiz.setLevel(getattr(logging, s.log_level.upper(), logging.INFO))
    raiz.handlers.clear()
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(_JsonFormatter() if s.log_formato == "json" else _TextoFormatter())
    raiz.addHandler(h)
    raiz.propagate = False


class Cronometro:
    """Mide duración de una transacción para el log: with Cronometro() as c: ...; c.ms"""

    def __enter__(self) -> "Cronometro":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ms = int((time.perf_counter() - self._t0) * 1000)
