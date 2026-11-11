"""Orquestación de la etapa Design (digest, alternatives, adversarial_review, compile)."""

from __future__ import annotations

from typing import Any

from kallicode_core.grafo import GrafoCliente

from . import etapas
from .cliente_interno import ClienteInterno
from .etapas import _grafo_dict, _riesgo_alternativa, _ruta_segura

PLANTILLAS_DESIGN: tuple[str, ...] = (
    "design.digest",
    "design.alternatives",
    "design.adversarial_review",
    "design.compile",
)


async def ejecutar_diseno(
    job: dict[str, Any],
    cliente: ClienteInterno,
    *,
    intento: int,
    grafo: GrafoCliente | None = None,
) -> None:
    """Ejecuta las cuatro plantillas de diseño y publica la versión de spec."""
    await etapas.ejecutar_diseno(job, cliente, intento=intento, grafo=grafo)


__all__ = [
    "PLANTILLAS_DESIGN",
    "_grafo_dict",
    "_riesgo_alternativa",
    "_ruta_segura",
    "ejecutar_diseno",
]
