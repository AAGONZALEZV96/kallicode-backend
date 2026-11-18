"""Logging estructurado en JSON para el pipeline de ingesta.

Cada línea de log es un objeto JSON con: timestamp, level, message, trace_id, tenant_id, phase y
cualquier campo pasado con ``extra=``.

KC-CM-12-6: toda línea pasa por ``redaccion`` antes de escribirse (sin credenciales, rutas
absolutas ni código del cliente) y una excepción se resume en tipo, mensaje redactado y ubicación
del último frame: nunca se escribe la traza completa, que arrastra rutas y líneas de código.
"""

import json
import logging
import os
import sys
import traceback
from datetime import UTC, datetime
from typing import Any

from .redaccion import mensaje_de_error, redactar, redactar_valor

RAIZ = "codemapping"

# Atributos propios de LogRecord: todo lo demás llegó por extra= y se emite.
_ATRIBUTOS_ESTANDAR = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}
_CONTEXTO = ("trace_id", "tenant_id", "phase")


def resumen_excepcion(exc: BaseException) -> dict[str, Any]:
    """Tipo, mensaje redactado y archivo:línea del último frame (sin ruta ni código fuente)."""
    frames = traceback.extract_tb(exc.__traceback__)
    donde = f"{os.path.basename(frames[-1].filename)}:{frames[-1].lineno}" if frames else None
    return {"tipo": type(exc).__name__, "mensaje": mensaje_de_error(exc), "donde": donde}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "message": redactar(record.getMessage()),
        }
        for key in _CONTEXTO:
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = redactar_valor(value, key)
        for key, value in record.__dict__.items():
            if key in _ATRIBUTOS_ESTANDAR or key in _CONTEXTO or key == "extra_fields":
                continue
            payload[key] = redactar_valor(value, key)
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(redactar_valor(extra))
        if record.exc_info and record.exc_info[1] is not None:
            payload["exc"] = resumen_excepcion(record.exc_info[1])
        return json.dumps(payload, ensure_ascii=False, default=str)


def get_logger(name: str = RAIZ) -> logging.Logger:
    """Logger del paquete. Solo la raíz «codemapping» lleva handler; los hijos propagan a ella.

    Un handler por hijo duplicaba cada línea (el hijo la escribía y la raíz otra vez).
    """
    raiz = logging.getLogger(RAIZ)
    if not raiz.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        raiz.addHandler(handler)
        raiz.setLevel(logging.INFO)
    if name != RAIZ and not name.startswith(RAIZ + "."):
        name = f"{RAIZ}.{name}"
    return logging.getLogger(name)
