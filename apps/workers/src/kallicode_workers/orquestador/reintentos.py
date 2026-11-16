"""Reglas de reintento y cuarentena del pipeline (KC-BE-06-4).

Fuente única de verdad para:
  - Límite de fallos por criterio (config: pipeline_max_fallos_por_criterio).
  - Límite de reintentos por fallo (config: pipeline_max_reintentos_por_fallo).
  - Marca de flaky y entrada en cuarentena por el contrato de Testing.

No guarda estado en memoria: se deriva de job["iteraciones"]["por_criterio"]
y de la matriz de QA persistida en core.qa_results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from kallicode_core.config import get_settings
from kallicode_core.logging import log

AccionReintento = Literal["continuar", "reintentar", "flaky", "escalar"]


@dataclass(frozen=True)
class DecisionReintento:
    """Resultado de evaluar una matriz de QA contra las reglas de reintento."""

    accion: AccionReintento
    motivo: str | None = None
    criterios_afectados: list[str] = field(default_factory=list)


def evaluar_matriz(
    matriz: list[dict[str, Any]],
    fallos_previos: dict[str, int],
) -> DecisionReintento:
    """Decide qué hacer con la matriz de QA de un intento.

    Reglas:
      1. Si un criterio falla N veces (>= pipeline_max_fallos_por_criterio),
         escalar con motivo "validacion_persistente" y detener el job.
      2. Si un criterio falla y pasa en el reintento, marcarlo flaky.
      3. Si todos pasan, continuar.
      4. Si algún criterio falla pero aún no llega al límite, reintentar.
    """
    s = get_settings()
    max_fallos = s.pipeline_max_fallos_por_criterio
    max_reintentos = s.pipeline_max_reintentos_por_fallo

    fallos_duros: list[str] = []
    flaky: list[str] = []
    fallos_pendientes: list[str] = []

    for fila in matriz:
        criterio = fila["criterio_id"]
        estado = fila["resultado"]
        previos = fallos_previos.get(criterio, 0)

        if estado == "flaky":
            flaky.append(criterio)
            continue

        if estado == "pasa":
            continue

        # estado == "falla"
        total_fallos = previos + 1
        if total_fallos >= max_fallos or total_fallos > max_reintentos:
            fallos_duros.append(criterio)
        else:
            fallos_pendientes.append(criterio)

    if fallos_duros:
        log.warning(
            "pipeline.escalado_por_criterio",
            criterios=fallos_duros,
            max_fallos=max_fallos,
        )
        return DecisionReintento(
            accion="escalar",
            motivo="validacion_persistente",
            criterios_afectados=fallos_duros,
        )

    if flaky:
        log.info("pipeline.flaky_detectado", criterios=flaky)
        return DecisionReintento(accion="flaky", criterios_afectados=flaky)

    if fallos_pendientes:
        return DecisionReintento(
            accion="reintentar",
            criterios_afectados=fallos_pendientes,
        )

    return DecisionReintento(accion="continuar")


def contar_fallos_por_criterio(matriz: list[dict[str, Any]]) -> dict[str, int]:
    """Devuelve {criterio_id: n_fallos} para acumular en job["iteraciones"]."""
    conteo: dict[str, int] = {}
    for fila in matriz:
        if fila["resultado"] == "falla":
            cid = fila["criterio_id"]
            conteo[cid] = conteo.get(cid, 0) + 1
    return conteo


def acumular_fallos(
    fallos_previos: dict[str, int],
    nuevos: dict[str, int],
) -> dict[str, int]:
    """Suma los fallos nuevos a los previos, criterio a criterio."""
    acumulado = dict(fallos_previos)
    for cid, n in nuevos.items():
        acumulado[cid] = acumulado.get(cid, 0) + n
    return acumulado
