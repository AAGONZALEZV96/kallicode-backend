"""Orquestación de la etapa Build (plan, diff por archivo con scope guard, selfreview y LOC)."""

from __future__ import annotations

from typing import Any

from kallicode_core.grafo import GrafoCliente

from . import etapas
from .cliente_interno import ClienteInterno
from .etapas import (
    _cargar_spec_aprobada,
    _contar_loc,
    _ruta_en_scope,
    _ruta_segura,
    _validar_diff_scope,
)

PLANTILLAS_BUILD: tuple[str, ...] = (
    "build.plan",
    "build.diff",
    "build.selfreview",
)


async def ejecutar_build(
    job: dict[str, Any],
    cliente: ClienteInterno,
    *,
    intento: int,
    grafo: GrafoCliente | None = None,
) -> dict[str, Any] | None:
    """Ejecuta plan, diffs bajo scope_paths y self-review; registra LOC si hay PR."""
    return await etapas.ejecutar_build(job, cliente, intento=intento, grafo=grafo)


__all__ = [
    "PLANTILLAS_BUILD",
    "_cargar_spec_aprobada",
    "_contar_loc",
    "_ruta_en_scope",
    "_ruta_segura",
    "_validar_diff_scope",
    "ejecutar_build",
]
