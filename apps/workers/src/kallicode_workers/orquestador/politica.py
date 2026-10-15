"""Política de reintentos y escalado del orquestador.

Regla del documento técnico: "un reintento por fallo; si pasa en el reintento
se marca flaky y entra en cuarentena". Si un criterio falla dos veces, se
escala a humano. Aquí se implementa la decisión de alto nivel: dado el estado
de una etapa, el número de intento y el error (si lo hubo), ¿qué hay que hacer?
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Máximo de intentos por etapa antes de escalar a humano.
# Coincide con el límite del documento técnico ("falla dos veces → humano").
MAX_INTENTOS_POR_ETAPA = 2


@dataclass(frozen=True)
class Decision:
    """Decisión sobre qué hacer con el job tras ejecutar la etapa."""

    accion: Literal["avanzar", "reintentar", "escalar"]
    motivo: str | None = None
    severidad: str | None = None


def decidir(etapa: str, intento: int, error: Exception | None) -> Decision:
    """Decide la acción a tomar.

    - Sin error: avanzar al siguiente estado.
    - Con error y quedan intentos: reintentar (el bucle lo volverá a tomar).
    - Con error y agotados los intentos: escalar a humano.
    """
    if error is None:
        return Decision(accion="avanzar")

    if intento < MAX_INTENTOS_POR_ETAPA:
        return Decision(accion="reintentar")

    # Escalado a humano. Severidad según la criticidad de la etapa.
    severidad = "alta" if etapa in {"triage", "design", "build", "seguridad"} else "media"
    return Decision(
        accion="escalar",
        motivo="validacion_persistente",
        severidad=severidad,
    )
