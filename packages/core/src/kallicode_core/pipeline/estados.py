"""Fuente canónica única para los estados y transiciones del pipeline KalliCode.

Define los 16 estados del ciclo de vida de un Job y la matriz de transiciones
válidas de la máquina de estados.
"""

from enum import StrEnum
from typing import Final


class EstadoJob(StrEnum):
    """Los 16 estados finitos del ciclo de vida de un Job en la fábrica."""

    # 1. Inicio y Admisión
    CREADO = "creado"
    TRIAGE = "triage"

    # 2. Etapa de Diseño
    DESIGN = "design"
    GATE1_ESPERA = "gate1_espera"
    GATE1_RECHAZADO = "gate1_rechazado"

    # 3. Etapa de Construcción (Build)
    BUILD = "build"
    VERIFICACION_INTERNA = "verificacion_interna"
    GATE2_ESPERA = "gate2_espera"
    GATE2_RECHAZADO = "gate2_rechazado"

    # 4. Control de Calidad y Seguridad
    QA = "qa"
    SEGURIDAD = "seguridad"
    GATE3_ESPERA = "gate3_espera"
    GATE3_RECHAZADO = "gate3_rechazado"

    # 5. Despliegue y Cierre
    DEPLOY = "deploy"
    COMPLETADO = "completado"

    # 6. Estados de Fallo / Escape
    FALLIDO = "fallido"


# Matriz canónica de transiciones válidas: EstadoOrigen -> Set[EstadoDestino]
# Cualquier transición fuera de este mapa es una violación de integridad.
TRANSICIONES_VALIDAS: Final[dict[EstadoJob, frozenset[EstadoJob]]] = {
    EstadoJob.CREADO: frozenset(
        {
            EstadoJob.TRIAGE,
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.TRIAGE: frozenset(
        {
            EstadoJob.DESIGN,
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.DESIGN: frozenset(
        {
            EstadoJob.GATE1_ESPERA,
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.GATE1_ESPERA: frozenset(
        {
            EstadoJob.BUILD,  # Aprobado por humano
            EstadoJob.GATE1_RECHAZADO,  # Rechazado por humano
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.GATE1_RECHAZADO: frozenset(
        {
            EstadoJob.DESIGN,  # Regreso a replanificar diseño
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.BUILD: frozenset(
        {
            EstadoJob.VERIFICACION_INTERNA,
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.VERIFICACION_INTERNA: frozenset(
        {
            EstadoJob.GATE2_ESPERA,
            EstadoJob.BUILD,  # Fallo determinista / loop de corrección
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.GATE2_ESPERA: frozenset(
        {
            EstadoJob.QA,  # Aprobado por humano
            EstadoJob.GATE2_RECHAZADO,  # Rechazado por humano
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.GATE2_RECHAZADO: frozenset(
        {
            EstadoJob.BUILD,  # Regreso a corregir código
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.QA: frozenset(
        {
            EstadoJob.SEGURIDAD,
            EstadoJob.BUILD,  # Regresión encontrada por QA
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.SEGURIDAD: frozenset(
        {
            EstadoJob.GATE3_ESPERA,
            EstadoJob.BUILD,  # Vulnerabilidad encontrada (requiere fix)
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.GATE3_ESPERA: frozenset(
        {
            EstadoJob.DEPLOY,  # Aprobación final de producción
            EstadoJob.GATE3_RECHAZADO,
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.GATE3_RECHAZADO: frozenset(
        {
            EstadoJob.DESIGN,  # Rechazo arquitectónico mayor
            EstadoJob.BUILD,  # Rechazo de implementación
            EstadoJob.FALLIDO,
        }
    ),
    EstadoJob.DEPLOY: frozenset(
        {
            EstadoJob.COMPLETADO,
            EstadoJob.FALLIDO,  # Fallo de verificación post-deploy
        }
    ),
    # Estados terminales (no permiten nuevas transiciones salientes)
    EstadoJob.COMPLETADO: frozenset(),
    EstadoJob.FALLIDO: frozenset(),
}


def es_transicion_valida(origen: EstadoJob | str, destino: EstadoJob | str) -> bool:
    """Valida deterministamente si el salto entre origen y destino está permitido."""
    try:
        est_origen = EstadoJob(origen)
        est_destino = EstadoJob(destino)
    except ValueError:
        return False

    destinos_permitidos = TRANSICIONES_VALIDAS.get(est_origen, frozenset())
    return est_destino in destinos_permitidos


def obtener_destinos_posibles(origen: EstadoJob | str) -> frozenset[EstadoJob]:
    """Obtiene el conjunto inmutable de estados hacia donde puede avanzar un origen."""
    try:
        est_origen = EstadoJob(origen)
    except ValueError:
        return frozenset()
    return TRANSICIONES_VALIDAS.get(est_origen, frozenset())
