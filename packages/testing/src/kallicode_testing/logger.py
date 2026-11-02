"""Configuración de logging estructurado JSON para trazabilidad de pruebas."""

import sys
from typing import Any, cast

import structlog


def configure_qa_logger() -> None:
    """Configura el pipeline de structlog para emitir logs en formato JSON."""
    if structlog.is_configured():
        return

    structlog.configure(
        processors=[
            structlog.processors.add_log_level,  # Procesador puro nativo
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        wrapper_class=structlog.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(**kwargs: Any) -> structlog.BoundLogger:
    """Retorna un logger pre-configurado con el contexto basal."""
    configure_qa_logger()
    # Inyectamos el nombre del logger directamente en el bind
    return cast(structlog.BoundLogger, structlog.get_logger().bind(logger="kallicode_qa", **kwargs))
