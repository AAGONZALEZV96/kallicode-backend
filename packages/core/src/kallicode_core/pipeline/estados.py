"""Fuente canónica única para los estados y transiciones del pipeline KalliCode.

Los 16 estados y sus transiciones válidas coinciden exactamente con:
  - El CHECK `job_estado_chk` de core.jobs (db/01_schema.sql).
  - El diccionario TRANSICIONES del router /internal/v1.

Cualquier divergencia entre este módulo y el esquema de la base de datos es
un bug: el esquema es la fuente de verdad (decisión D06).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class EstadoJob(StrEnum):
    """Los 16 estados finitos del ciclo de vida de un Job en la fábrica.

    Coinciden literalmente con el CHECK de core.jobs (`job_estado_chk`) y con
    los nombres que consume el endpoint PATCH /internal/v1/jobs/{id}/state.
    """

    # 1. Admisión y triage
    PENDIENTE_ASIGNACION = "pendiente_asignacion"
    TRIAGE = "triage"

    # 2. Diseño y primer gate
    DESIGN = "design"
    GATE1 = "gate1"

    # 3. Build y segundo gate
    BUILD = "build"
    QA = "qa"
    GATE2 = "gate2"

    # 4. Seguridad y tercer gate
    SECURITY = "security"
    DEPLOY_PREFLIGHT = "deploy_preflight"
    GATE3 = "gate3"

    # 5. Deploy y producción
    DEPLOY = "deploy"
    PRODUCCION = "produccion"

    # 6. Estados de escape
    PAUSADO_POR_CUOTA = "pausado_por_cuota"
    ESCALADO_HUMANO = "escalado_humano"
    CANCELADO = "cancelado"
    CERRADO_DUPLICADO = "cerrado_duplicado"


# Estados terminales: no admiten transiciones salientes.
TERMINALES: Final[frozenset[EstadoJob]] = frozenset(
    {
        EstadoJob.PRODUCCION,
        EstadoJob.CANCELADO,
        EstadoJob.CERRADO_DUPLICADO,
    }
)

# Estados de gate: la única salida es la firma humana (endpoints del portal).
GATES: Final[dict[EstadoJob, int]] = {
    EstadoJob.GATE1: 1,
    EstadoJob.GATE2: 2,
    EstadoJob.GATE3: 3,
}


# Matriz canónica de transiciones válidas: EstadoOrigen -> Set[EstadoDestino].
# Espejo exacto del diccionario TRANSICIONES del router /internal/v1.
TRANSICIONES_VALIDAS: Final[dict[EstadoJob, frozenset[EstadoJob]]] = {
    EstadoJob.PENDIENTE_ASIGNACION: frozenset(
        {
            EstadoJob.TRIAGE,
            EstadoJob.CANCELADO,
            EstadoJob.PAUSADO_POR_CUOTA,
        }
    ),
    EstadoJob.TRIAGE: frozenset(
        {
            EstadoJob.DESIGN,
            EstadoJob.CERRADO_DUPLICADO,
            EstadoJob.CANCELADO,
            EstadoJob.ESCALADO_HUMANO,
        }
    ),
    EstadoJob.DESIGN: frozenset(
        {
            EstadoJob.GATE1,
            EstadoJob.CANCELADO,
            EstadoJob.ESCALADO_HUMANO,
        }
    ),
    EstadoJob.GATE1: frozenset(
        {
            EstadoJob.BUILD,
            EstadoJob.DESIGN,
            EstadoJob.CANCELADO,
        }
    ),
    EstadoJob.BUILD: frozenset(
        {
            EstadoJob.QA,
            EstadoJob.DESIGN,
            EstadoJob.CANCELADO,
            EstadoJob.ESCALADO_HUMANO,
            EstadoJob.PAUSADO_POR_CUOTA,
        }
    ),
    EstadoJob.QA: frozenset(
        {
            EstadoJob.GATE2,
            EstadoJob.BUILD,
            EstadoJob.CANCELADO,
            EstadoJob.ESCALADO_HUMANO,
        }
    ),
    EstadoJob.GATE2: frozenset(
        {
            EstadoJob.SECURITY,
            EstadoJob.BUILD,
            EstadoJob.CANCELADO,
        }
    ),
    EstadoJob.SECURITY: frozenset(
        {
            EstadoJob.DEPLOY_PREFLIGHT,
            EstadoJob.BUILD,
            EstadoJob.CANCELADO,
            EstadoJob.ESCALADO_HUMANO,
        }
    ),
    EstadoJob.DEPLOY_PREFLIGHT: frozenset(
        {
            EstadoJob.GATE3,
            EstadoJob.BUILD,
            EstadoJob.CANCELADO,
        }
    ),
    EstadoJob.GATE3: frozenset(
        {
            EstadoJob.DEPLOY,
            EstadoJob.DESIGN,
            EstadoJob.BUILD,
            EstadoJob.QA,
            EstadoJob.CANCELADO,
        }
    ),
    EstadoJob.DEPLOY: frozenset(
        {
            EstadoJob.PRODUCCION,
            EstadoJob.ESCALADO_HUMANO,
            EstadoJob.CANCELADO,
        }
    ),
    EstadoJob.PAUSADO_POR_CUOTA: frozenset(
        {
            EstadoJob.TRIAGE,
            EstadoJob.BUILD,
            EstadoJob.CANCELADO,
        }
    ),
    EstadoJob.ESCALADO_HUMANO: frozenset(
        {
            EstadoJob.TRIAGE,
            EstadoJob.DESIGN,
            EstadoJob.BUILD,
            EstadoJob.QA,
            EstadoJob.SECURITY,
            EstadoJob.DEPLOY,
            EstadoJob.CANCELADO,
        }
    ),
    # Estados terminales.
    EstadoJob.PRODUCCION: frozenset(),
    EstadoJob.CANCELADO: frozenset(),
    EstadoJob.CERRADO_DUPLICADO: frozenset(),
}


def es_transicion_valida(origen: EstadoJob | str, destino: EstadoJob | str) -> bool:
    """Valida si el salto entre origen y destino está permitido."""
    try:
        est_origen = EstadoJob(origen)
        est_destino = EstadoJob(destino)
    except ValueError:
        return False

    return est_destino in TRANSICIONES_VALIDAS.get(est_origen, frozenset())


def obtener_destinos_posibles(origen: EstadoJob | str) -> frozenset[EstadoJob]:
    """Conjunto inmutable de estados hacia donde puede avanzar un origen."""
    try:
        est_origen = EstadoJob(origen)
    except ValueError:
        return frozenset()
    return TRANSICIONES_VALIDAS.get(est_origen, frozenset())
